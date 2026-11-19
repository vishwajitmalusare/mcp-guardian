"""
Stdio Interceptor Wrapper for Claude Desktop.
Acts as a transparent Man-in-the-Middle (MITM) proxy between Claude Desktop and local MCP servers.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import threading
import time
from typing import List, Optional
from mcp_guardian.core.audit import AuditLogger, default_audit_logger
from mcp_guardian.core.models import AuditEntry, ClientType, Decision, DecisionAction, MCPRequest
from mcp_guardian.core.parser import MCPParser
from mcp_guardian.core.policy import PolicyEngine
from mcp_guardian.daemon.ipc import IPCClient
from mcp_guardian.gui.popup import ask_user_confirmation_sync

logger = logging.getLogger("mcp_guardian.stdio")

# Ensure standard streams use UTF-8 encoding on Windows
for stream_name in ("stdin", "stdout", "stderr"):
    stream = getattr(sys, stream_name, None)
    if stream and hasattr(stream, "reconfigure"):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


class StdioWrapper:
    """Manages interception of standard I/O communication between Claude Desktop and an MCP server."""

    def __init__(
        self,
        command_args: List[str],
        server_name: str = "default_server",
        audit_logger: Optional[AuditLogger] = None,
        policy_engine: Optional[PolicyEngine] = None,
        ipc_port: Optional[int] = None,
    ):
        self.command_args = command_args
        self.server_name = server_name
        self.audit = audit_logger or default_audit_logger
        self.policy = policy_engine or PolicyEngine()
        self.ipc_client = IPCClient(port=ipc_port) if ipc_port else IPCClient()

        self.proc: Optional[subprocess.Popen] = None
        self.running = False
        self._stdout_lock = threading.Lock()
        self._child_stdin_lock = threading.Lock()

    def _write_to_claude_stdout(self, line: str) -> None:
        """Thread-safe write to Claude Desktop's standard input using UTF-8."""
        with self._stdout_lock:
            try:
                if hasattr(sys.stdout, "buffer"):
                    sys.stdout.buffer.write(line.encode("utf-8", errors="replace"))
                    sys.stdout.buffer.flush()
                else:
                    sys.stdout.write(line)
                    sys.stdout.flush()
            except Exception:
                try:
                    sys.stdout.write(line.encode("ascii", errors="replace").decode("ascii"))
                    sys.stdout.flush()
                except Exception:
                    pass

    def _write_to_child_stdin(self, line: str) -> None:
        """Thread-safe write to downstream MCP server's standard input."""
        with self._child_stdin_lock:
            if self.proc and self.proc.stdin and not self.proc.stdin.closed:
                self.proc.stdin.write(line.encode("utf-8"))
                self.proc.stdin.flush()

    def _authorize_request(self, request: MCPRequest) -> Decision:
        """Obtains authorization decision via central IPC Daemon or local sync popup."""
        # 1. First check if IPC Daemon is running
        if self.ipc_client.is_daemon_alive():
            daemon_decision = self.ipc_client.request_decision_from_daemon(request)
            if daemon_decision is not None:
                return daemon_decision

        # 2. Check session policy
        session_dec = self.policy.get_session_decision(request)
        if session_dec is not None:
            return session_dec

        # 3. Fallback to standalone popup modal
        return ask_user_confirmation_sync(request, self.policy.default_timeout_seconds)

    def _handle_client_message(self, line: str) -> None:
        """Processes a single line sent by Claude Desktop."""
        parsed = MCPParser.parse_line(line)
        if not parsed:
            # Not valid JSON or empty; pass through to child
            self._write_to_child_stdin(line)
            return

        # Check if message requires interception
        if not MCPParser.is_interceptable(parsed):
            self._write_to_child_stdin(line)
            return

        # Extract MCPRequest
        req = MCPParser.extract_request(
            parsed,
            server_name=self.server_name,
            client_type=ClientType.CLAUDE_DESKTOP,
        )
        if not req:
            self._write_to_child_stdin(line)
            return

        start_time = time.time()
        # Request human authorization
        decision = self._authorize_request(req)
        duration_ms = (time.time() - start_time) * 1000.0

        # Store session rule if requested
        self.policy.record_decision(req, decision)

        # Record to audit log
        entry = AuditEntry(
            request=req,
            decision=decision,
            duration_ms=duration_ms,
            downstream_status="COMPLETED" if decision.is_allowed else "BLOCKED",
        )
        self.audit.record(entry)

        if decision.is_allowed:
            # Forward genuine request to downstream MCP server
            self._write_to_child_stdin(line)
        else:
            # Block request: do NOT forward to child. Return synthetic denial response to Claude Desktop
            denial_resp = MCPParser.make_blocked_tool_response(
                req_id=req.id,
                tool_name=req.tool_name,
                reason=decision.reason,
            )
            resp_line = MCPParser.serialize(denial_resp)
            self._write_to_claude_stdout(resp_line)

    def _claude_reader_loop(self) -> None:
        """Reads incoming lines from Claude Desktop (sys.stdin)."""
        try:
            while self.running:
                raw_line = sys.stdin.readline()
                if not raw_line:
                    break
                # Handle each message (dispatch to thread pool or execute)
                # Spawning a thread per message allows parallel tool calls to be processed concurrently!
                threading.Thread(target=self._handle_client_message, args=(raw_line,), daemon=True).start()
        except Exception as e:
            logger.error(f"Error reading from Claude stdin: {e}")
        finally:
            self.running = False
            self._terminate_child()

    def _child_stdout_reader_loop(self) -> None:
        """Reads stdout lines from downstream MCP server and forwards to Claude Desktop."""
        try:
            if not self.proc or not self.proc.stdout:
                return
            while self.running and self.proc:
                raw_bytes = self.proc.stdout.readline()
                if not raw_bytes:
                    break
                line = raw_bytes.decode("utf-8", errors="replace")
                self._write_to_claude_stdout(line)
        except Exception as e:
            logger.error(f"Error reading from child stdout: {e}")
        finally:
            self.running = False

    def _child_stderr_reader_loop(self) -> None:
        """Reads stderr lines from downstream MCP server and forwards to sys.stderr."""
        try:
            if not self.proc or not self.proc.stderr:
                return
            while self.running and self.proc:
                raw_bytes = self.proc.stderr.readline()
                if not raw_bytes:
                    break
                sys.stderr.write(raw_bytes.decode("utf-8", errors="replace"))
                sys.stderr.flush()
        except Exception:
            pass

    def _terminate_child(self) -> None:
        """Gracefully terminates downstream process."""
        if self.proc:
            try:
                self.proc.terminate()
                self.proc.wait(timeout=2.0)
            except Exception:
                try:
                    self.proc.kill()
                except Exception:
                    pass

    def start(self) -> int:
        """Spawns the downstream server and begins bidirectional proxying."""
        self.running = True
        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUTF8"] = "1"
        try:
            self.proc = subprocess.Popen(
                self.command_args,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                bufsize=0,
                env=env,
            )
        except Exception as e:
            sys.stderr.write(f"[MCP Guardian] Failed to spawn target process {self.command_args}: {e}\n")
            sys.stderr.flush()
            return 1

        # Start background streaming threads
        t_stdin = threading.Thread(target=self._claude_reader_loop, daemon=True)
        t_stdout = threading.Thread(target=self._child_stdout_reader_loop, daemon=True)
        t_stderr = threading.Thread(target=self._child_stderr_reader_loop, daemon=True)

        t_stdin.start()
        t_stdout.start()
        t_stderr.start()

        # Wait until child exits or stdin closes
        try:
            return_code = self.proc.wait()
            return return_code
        except KeyboardInterrupt:
            self._terminate_child()
            return 0
        finally:
            self._terminate_child()
