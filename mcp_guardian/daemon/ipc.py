"""
Inter-Process Communication (IPC) for MCP Guardian.
Provides robust local IPC between wrapper processes and the central daemon,
using a hybrid transport (File-based atomic mailbox + Socket) to operate flawlessly
in any environment, including sandboxes and corporate restricted networks.
"""

from __future__ import annotations

import json
import logging
import os
import socket
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, Optional
from mcp_guardian.core.models import Decision, DecisionAction, MCPRequest

logger = logging.getLogger("mcp_guardian.ipc")

DEFAULT_IPC_HOST = os.environ.get("MCP_GUARDIAN_IPC_HOST", "127.0.0.1")
DEFAULT_IPC_PORT = int(os.environ.get("MCP_GUARDIAN_IPC_PORT", "8766"))


def get_default_ipc_dir() -> Path:
    """Returns the IPC directory path."""
    env_dir = os.environ.get("MCP_GUARDIAN_IPC_DIR")
    if env_dir:
        p = Path(env_dir)
    else:
        # Prefer workspace local .mcp_guardian/ipc
        p = Path(os.getcwd()) / ".mcp_guardian" / "ipc"
    p.mkdir(parents=True, exist_ok=True)
    (p / "requests").mkdir(parents=True, exist_ok=True)
    (p / "responses").mkdir(parents=True, exist_ok=True)
    return p


class IPCClient:
    """Used by Stdio Wrapper instances to talk to the central Guardian Daemon."""

    def __init__(
        self,
        host: str = DEFAULT_IPC_HOST,
        port: int = DEFAULT_IPC_PORT,
        timeout: float = 65.0,
        ipc_dir: Optional[Path] = None,
    ):
        self.host = host
        self.port = port
        self.timeout = timeout
        self.ipc_dir = ipc_dir or get_default_ipc_dir()
        self.requests_dir = self.ipc_dir / "requests"
        self.responses_dir = self.ipc_dir / "responses"
        self.heartbeat_file = self.ipc_dir / "daemon.heartbeat"

    def is_daemon_alive(self) -> bool:
        """Checks if the central daemon is running via heartbeat or socket."""
        # 1. Check file-based heartbeat
        try:
            if self.heartbeat_file.exists():
                mtime = self.heartbeat_file.stat().st_mtime
                if time.time() - mtime < 3.5:
                    return True
                else:
                    # Clean up stale heartbeat file from an exited daemon
                    try:
                        self.heartbeat_file.unlink()
                    except Exception:
                        pass
        except Exception:
            pass

        # 2. Check TCP socket fallback
        try:
            with socket.create_connection((self.host, self.port), timeout=0.3):
                return True
        except Exception:
            return False

        return False

    def request_decision_from_daemon(self, request: MCPRequest) -> Optional[Decision]:
        """Submits an MCPRequest to the central daemon and waits for decision."""
        if not self.is_daemon_alive():
            return None

        # Method 1: File-based Mailbox IPC (Fast, reliable, zero network dependency)
        try:
            dec = self._request_via_file_mailbox(request)
            if dec is not None:
                return dec
        except Exception as e:
            logger.debug(f"File IPC attempt failed: {e}")

        # Method 2: Socket IPC
        try:
            return self._request_via_socket(request)
        except Exception:
            return None

    def _request_via_file_mailbox(self, request: MCPRequest) -> Optional[Decision]:
        req_id_safe = str(request.id).replace("/", "_").replace("\\", "_")
        req_file = self.requests_dir / f"req_{req_id_safe}.json"
        tmp_file = self.requests_dir / f"req_{req_id_safe}_{uuid.uuid4().hex[:6]}.tmp"
        resp_file = self.responses_dir / f"resp_{req_id_safe}.json"

        # 1. Clean any stale response from an earlier session
        if resp_file.exists():
            try:
                resp_file.unlink()
            except Exception:
                pass

        # 2. Write request atomically
        data = {
            "action": "AUTHORIZE_REQUEST",
            "request": request.to_dict(),
            "timestamp": time.time(),
        }
        with open(tmp_file, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        try:
            tmp_file.replace(req_file)
        except Exception:
            try:
                if req_file.exists():
                    req_file.unlink()
                tmp_file.replace(req_file)
            except Exception:
                pass

        # 3. Poll for response
        start_time = time.time()
        while time.time() - start_time < self.timeout:
            if resp_file.exists():
                try:
                    with open(resp_file, "r", encoding="utf-8") as f:
                        res_dict = json.load(f)
                    try:
                        resp_file.unlink()
                    except Exception:
                        pass

                    if res_dict.get("status") == "OK" and "decision" in res_dict:
                        dec_data = res_dict["decision"]
                        return Decision(
                            request_id=dec_data["request_id"],
                            action=DecisionAction(dec_data["action"]),
                            reason=dec_data.get("reason", ""),
                            remember_session=dec_data.get("remember_session", False),
                            timestamp=dec_data.get("timestamp"),
                        )
                except Exception:
                    pass
            time.sleep(0.02)  # 20ms polling interval

        return None

    def _request_via_socket(self, request: MCPRequest) -> Optional[Decision]:
        sock = socket.create_connection((self.host, self.port), timeout=self.timeout)
        sock.settimeout(self.timeout)
        with sock:
            payload = {
                "action": "AUTHORIZE_REQUEST",
                "request": request.to_dict(),
            }
            msg_bytes = (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8")
            sock.sendall(msg_bytes)

            response_data = b""
            while b"\n" not in response_data:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                response_data += chunk

            if not response_data:
                return None

            res_dict = json.loads(response_data.decode("utf-8").strip())
            if res_dict.get("status") == "OK" and "decision" in res_dict:
                dec_data = res_dict["decision"]
                return Decision(
                    request_id=dec_data["request_id"],
                    action=DecisionAction(dec_data["action"]),
                    reason=dec_data.get("reason", ""),
                    remember_session=dec_data.get("remember_session", False),
                    timestamp=dec_data.get("timestamp"),
                )


class IPCServer:
    """Embedded inside the central daemon / dashboard to process requests from wrappers."""

    def __init__(
        self,
        request_handler: Callable[[MCPRequest], Decision],
        host: str = DEFAULT_IPC_HOST,
        port: int = DEFAULT_IPC_PORT,
        ipc_dir: Optional[Path] = None,
    ):
        self.request_handler = request_handler
        self.host = host
        self.port = port
        self.ipc_dir = ipc_dir or get_default_ipc_dir()
        self.requests_dir = self.ipc_dir / "requests"
        self.responses_dir = self.ipc_dir / "responses"
        self.heartbeat_file = self.ipc_dir / "daemon.heartbeat"

        self.running = False
        self._server_sock: Optional[socket.socket] = None
        self._file_thread: Optional[threading.Thread] = None
        self._heartbeat_thread: Optional[threading.Thread] = None
        self._socket_thread: Optional[threading.Thread] = None

    def start(self) -> None:
        """Starts both file-based watcher and socket listener."""
        self.running = True

        # Clean old files
        for f in list(self.requests_dir.glob("*")):
            try:
                f.unlink()
            except Exception:
                pass
        for f in list(self.responses_dir.glob("*")):
            try:
                f.unlink()
            except Exception:
                pass

        self.heartbeat_file.touch()

        # 1. Heartbeat thread
        self._heartbeat_thread = threading.Thread(target=self._heartbeat_loop, daemon=True)
        self._heartbeat_thread.start()

        # 2. File mailbox loop
        self._file_thread = threading.Thread(target=self._file_mailbox_loop, daemon=True)
        self._file_thread.start()

        # 3. Socket listener (optional, handles failures gracefully)
        try:
            self._server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self._server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self._server_sock.bind((self.host, self.port))
            self._server_sock.listen(16)
            self._socket_thread = threading.Thread(target=self._socket_listen_loop, daemon=True)
            self._socket_thread.start()
            logger.info(f"Socket IPC listening on {self.host}:{self.port}")
        except Exception as e:
            logger.debug(f"Socket IPC could not bind (File IPC will be used): {e}")

        logger.info(f"File-based IPC active in {self.ipc_dir}")

    def _heartbeat_loop(self) -> None:
        while self.running:
            try:
                self.heartbeat_file.touch()
            except Exception:
                pass
            time.sleep(1.0)

    def _file_mailbox_loop(self) -> None:
        """Watches requests directory for incoming requests."""
        while self.running:
            try:
                for req_path in list(self.requests_dir.glob("req_*.json")):
                    if not self.running:
                        break
                    # Process in a worker thread so multiple requests can be handled simultaneously
                    threading.Thread(target=self._process_file_request, args=(req_path,), daemon=True).start()
            except Exception:
                pass
            time.sleep(0.02)

    def _process_file_request(self, req_path: Path) -> None:
        try:
            # Try reading request
            with open(req_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            # Remove request file so it isn't picked up again
            try:
                req_path.unlink()
            except Exception:
                pass

            if data.get("action") == "AUTHORIZE_REQUEST" and "request" in data:
                req = MCPRequest.from_dict(data["request"])
                # Request human decision
                decision = self.request_handler(req)

                req_id_safe = str(req.id).replace("/", "_").replace("\\", "_")
                resp_file = self.responses_dir / f"resp_{req_id_safe}.json"
                tmp_file = self.responses_dir / f"resp_{req_id_safe}_{uuid.uuid4().hex[:6]}.tmp"

                res_payload = {"status": "OK", "decision": decision.to_dict()}
                with open(tmp_file, "w", encoding="utf-8") as f:
                    json.dump(res_payload, f, ensure_ascii=False)
                try:
                    tmp_file.replace(resp_file)
                except Exception:
                    try:
                        if resp_file.exists():
                            resp_file.unlink()
                        tmp_file.replace(resp_file)
                    except Exception:
                        pass
        except Exception as e:
            logger.error(f"Error processing file IPC request: {e}")

    def _socket_listen_loop(self) -> None:
        while self.running and self._server_sock:
            try:
                client_sock, _ = self._server_sock.accept()
                threading.Thread(target=self._handle_socket_client, args=(client_sock,), daemon=True).start()
            except Exception:
                break

    def _handle_socket_client(self, sock: socket.socket) -> None:
        with sock:
            try:
                buffer = b""
                while b"\n" not in buffer:
                    chunk = sock.recv(4096)
                    if not chunk:
                        break
                    buffer += chunk

                if not buffer:
                    return

                msg_str = buffer.decode("utf-8").strip()
                data = json.loads(msg_str)

                if data.get("action") == "AUTHORIZE_REQUEST" and "request" in data:
                    req = MCPRequest.from_dict(data["request"])
                    decision = self.request_handler(req)
                    res_bytes = (
                        json.dumps(
                            {"status": "OK", "decision": decision.to_dict()},
                            ensure_ascii=False,
                        )
                        + "\n"
                    ).encode("utf-8")
                    sock.sendall(res_bytes)
            except Exception as e:
                logger.error(f"Error handling socket IPC: {e}")

    def stop(self) -> None:
        self.running = False
        if self._server_sock:
            try:
                self._server_sock.close()
            except Exception:
                pass
        try:
            if self.heartbeat_file.exists():
                self.heartbeat_file.unlink()
        except Exception:
            pass
