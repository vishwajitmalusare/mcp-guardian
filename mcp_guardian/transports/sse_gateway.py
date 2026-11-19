"""
HTTP/SSE Reverse Proxy Gateway for Claude Web.
Implements the Model Context Protocol (MCP) Server-Sent Events (SSE) transport specification,
intercepting web-initiated tool invocations with desktop confirmation modals.
"""

from __future__ import annotations

import json
import logging
import queue
import subprocess
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qs, urlparse
from mcp_guardian.core.audit import AuditLogger, default_audit_logger
from mcp_guardian.core.models import AuditEntry, ClientType, Decision, DecisionAction, MCPRequest
from mcp_guardian.core.parser import MCPParser
from mcp_guardian.core.policy import PolicyEngine
from mcp_guardian.daemon.ipc import IPCClient
from mcp_guardian.gui.popup import ask_user_confirmation_sync

logger = logging.getLogger("mcp_guardian.sse")


class DownstreamStdioBridge:
    """Spawns and manages a downstream MCP stdio server to handle executed requests."""

    def __init__(self, command_args: List[str]):
        self.command_args = command_args
        self.proc: Optional[subprocess.Popen] = None
        self._lock = threading.Lock()
        self._pending_responses: Dict[Any, queue.Queue] = {}
        self._running = False

    def start(self) -> None:
        if not self.command_args:
            return
        self.proc = subprocess.Popen(
            self.command_args,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,
        )
        self._running = True
        threading.Thread(target=self._reader_loop, daemon=True).start()

    def _reader_loop(self) -> None:
        if not self.proc or not self.proc.stdout:
            return
        for raw_bytes in self.proc.stdout:
            if not raw_bytes:
                break
            line = raw_bytes.decode("utf-8", errors="replace").strip()
            parsed = MCPParser.parse_line(line)
            if parsed and "id" in parsed:
                req_id = parsed["id"]
                with self._lock:
                    q = self._pending_responses.get(req_id)
                if q:
                    q.put(parsed)

    def execute(self, message: Dict[str, Any], timeout: float = 30.0) -> Optional[Dict[str, Any]]:
        """Sends a JSON-RPC request to downstream server and waits for response."""
        req_id = message.get("id")
        resp_q: queue.Queue = queue.Queue()

        with self._lock:
            self._pending_responses[req_id] = resp_q

        try:
            line = json.dumps(message, ensure_ascii=False) + "\n"
            if self.proc and self.proc.stdin:
                self.proc.stdin.write(line.encode("utf-8"))
                self.proc.stdin.flush()

            response = resp_q.get(timeout=timeout)
            return response
        except queue.Empty:
            return None
        finally:
            with self._lock:
                self._pending_responses.pop(req_id, None)

    def stop(self) -> None:
        self._running = False
        if self.proc:
            try:
                self.proc.terminate()
            except Exception:
                pass


class SSESession:
    """Represents an active client SSE connection stream."""

    def __init__(self, session_id: str):
        self.session_id = session_id
        self.message_queue: queue.Queue = queue.Queue()
        self.is_active = True
        self.created_at = time.time()

    def push(self, message: Dict[str, Any]) -> None:
        if self.is_active:
            self.message_queue.put(message)

    def close(self) -> None:
        self.is_active = False


class MCPGatewayHandler(BaseHTTPRequestHandler):
    """Handles HTTP requests for SSE connections and JSON-RPC message submissions."""

    server_name: str = "web_mcp_server"
    sessions: Dict[str, SSESession] = {}
    downstream: Optional[DownstreamStdioBridge] = None
    audit_logger: AuditLogger = default_audit_logger
    policy: PolicyEngine = PolicyEngine()
    ipc_client: IPCClient = IPCClient()

    def _set_cors_headers(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")

    def do_OPTIONS(self) -> None:
        self.send_response(200)
        self._set_cors_headers()
        self.end_headers()

    def do_GET(self) -> None:
        """Handles SSE stream connection (GET /sse)."""
        parsed_url = urlparse(self.path)
        if parsed_url.path == "/sse":
            session_id = str(uuid.uuid4())
            session = SSESession(session_id)
            self.__class__.sessions[session_id] = session

            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self._set_cors_headers()
            self.end_headers()

            # Emit initial endpoint event as specified by MCP SSE standard
            endpoint_url = f"/messages?sessionId={session_id}"
            endpoint_event = f"event: endpoint\ndata: {endpoint_url}\n\n"
            self.wfile.write(endpoint_event.encode("utf-8"))
            self.wfile.flush()

            logger.info(f"[Claude Web] New SSE connection established (Session: {session_id})")

            # Stream loop
            try:
                while session.is_active:
                    try:
                        msg = session.message_queue.get(timeout=1.0)
                        raw_data = json.dumps(msg, ensure_ascii=False)
                        event_payload = f"event: message\ndata: {raw_data}\n\n"
                        self.wfile.write(event_payload.encode("utf-8"))
                        self.wfile.flush()
                    except queue.Empty:
                        # Send periodic SSE keep-alive comment
                        self.wfile.write(b": keep-alive\n\n")
                        self.wfile.flush()
            except (ConnectionError, BrokenPipeError, socket.error):
                pass
            finally:
                session.close()
                self.__class__.sessions.pop(session_id, None)
                logger.info(f"[Claude Web] SSE session {session_id} disconnected.")
        elif parsed_url.path == "/health":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self._set_cors_headers()
            self.end_headers()
            self.wfile.write(b'{"status": "UP", "service": "MCP Guardian Gateway"}')
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self) -> None:
        """Handles incoming JSON-RPC messages from Claude Web (POST /messages?sessionId=...)."""
        parsed_url = urlparse(self.path)
        if parsed_url.path == "/messages":
            query_params = parse_qs(parsed_url.query)
            session_id = query_params.get("sessionId", [None])[0]

            content_length = int(self.headers.get("Content-Length", 0))
            post_body = self.rfile.read(content_length).decode("utf-8")

            parsed = MCPParser.parse_line(post_body)
            if not parsed:
                self.send_response(400)
                self.end_headers()
                return

            # Acknowledge receipt of message
            self.send_response(202)
            self.send_header("Content-Type", "text/plain")
            self._set_cors_headers()
            self.end_headers()
            self.wfile.write(b"Accepted")

            # Process the message asynchronously
            session = self.__class__.sessions.get(session_id)
            threading.Thread(
                target=self._process_mcp_message,
                args=(parsed, session),
                daemon=True,
            ).start()
        else:
            self.send_response(404)
            self.end_headers()

    def _process_mcp_message(self, message: Dict[str, Any], session: Optional[SSESession]) -> None:
        """Processes message, triggers confirmation modal if interceptable, and returns response."""
        req_id = message.get("id")

        if not MCPParser.is_interceptable(message):
            # Pass through non-tool requests (initialize, tools/list, etc.) to downstream
            if self.__class__.downstream:
                resp = self.__class__.downstream.execute(message)
                if resp and session:
                    session.push(resp)
            return

        # Intercept tool call
        req = MCPParser.extract_request(
            message,
            server_name=self.__class__.server_name,
            client_type=ClientType.CLAUDE_WEB,
        )
        if not req:
            return

        start_time = time.time()
        # Obtain authorization decision
        decision = self._authorize_request(req)
        duration_ms = (time.time() - start_time) * 1000.0

        # Store session rule
        self.__class__.policy.record_decision(req, decision)

        # Audit log
        entry = AuditEntry(
            request=req,
            decision=decision,
            duration_ms=duration_ms,
            downstream_status="COMPLETED" if decision.is_allowed else "BLOCKED",
        )
        self.__class__.audit_logger.record(entry)

        if decision.is_allowed:
            # Forward to downstream server
            if self.__class__.downstream:
                real_resp = self.__class__.downstream.execute(message)
                if real_resp and session:
                    session.push(real_resp)
            else:
                # Mock execution fallback
                mock_resp = {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {
                        "content": [{"type": "text", "text": f"Successfully executed tool '{req.tool_name}'"}],
                        "isError": False,
                    },
                }
                if session:
                    session.push(mock_resp)
        else:
            # Blocked: send synthetic denial response via SSE
            denial_resp = MCPParser.make_blocked_tool_response(
                req_id=req_id,
                tool_name=req.tool_name,
                reason=decision.reason,
            )
            if session:
                session.push(denial_resp)

    def _authorize_request(self, request: MCPRequest) -> Decision:
        # 1. Check IPC Daemon
        if self.__class__.ipc_client.is_daemon_alive():
            daemon_dec = self.__class__.ipc_client.request_decision_from_daemon(request)
            if daemon_dec is not None:
                return daemon_dec

        # 2. Check session rules
        cached = self.__class__.policy.get_session_decision(request)
        if cached is not None:
            return cached

        # 3. Popup modal fallback
        return ask_user_confirmation_sync(request, self.__class__.policy.default_timeout_seconds)

    def log_message(self, format: str, *args: Any) -> None:
        """Suppress default HTTP server access logs to keep terminal clean."""
        pass


class SSEGatewayServer:
    """Manages the lifecycle of the Claude Web MCP SSE Gateway."""

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 8765,
        server_name: str = "claude_web_gateway",
        downstream_command: Optional[List[str]] = None,
        audit_logger: Optional[AuditLogger] = None,
    ):
        self.host = host
        self.port = port
        self.server_name = server_name
        self.audit = audit_logger or default_audit_logger

        self.downstream_bridge: Optional[DownstreamStdioBridge] = None
        if downstream_command:
            self.downstream_bridge = DownstreamStdioBridge(downstream_command)

        # Configure handler class
        MCPGatewayHandler.server_name = self.server_name
        MCPGatewayHandler.downstream = self.downstream_bridge
        MCPGatewayHandler.audit_logger = self.audit

        self.http_server: Optional[ThreadingHTTPServer] = None
        self._thread: Optional[threading.Thread] = None
        self.running = False

    def start(self) -> None:
        """Starts the gateway server in a background thread."""
        if self.downstream_bridge:
            self.downstream_bridge.start()

        self.http_server = ThreadingHTTPServer((self.host, self.port), MCPGatewayHandler)
        self.running = True
        self._thread = threading.Thread(target=self.http_server.serve_forever, daemon=True)
        self._thread.start()
        logger.info(f"SSE Gateway for Claude Web active on http://{self.host}:{self.port}/sse")

    def stop(self) -> None:
        """Stops the gateway server."""
        self.running = False
        if self.http_server:
            self.http_server.shutdown()
            self.http_server.server_close()
        if self.downstream_bridge:
            self.downstream_bridge.stop()
        logger.info("SSE Gateway stopped.")
