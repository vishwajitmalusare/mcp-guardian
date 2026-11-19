"""
Automated Mock Client simulating Claude Desktop and Claude Web.
Sends MCP tool invocations to test authorization popups, approvals, denials, and concurrency.
"""

from __future__ import annotations

import json
import logging
import queue
import subprocess
import sys
import threading
import time
import urllib.request
from typing import Any, Dict, List, Optional

logger = logging.getLogger("mcp_guardian.mock_client")


class MockStdioClient:
    """Simulates Claude Desktop communicating with an MCP server or wrapper via stdin/stdout."""

    def __init__(self, command_args: List[str]):
        self.command_args = command_args
        self.proc: Optional[subprocess.Popen] = None
        self._pending: Dict[Any, queue.Queue] = {}
        self._lock = threading.Lock()
        self.running = False

    def start(self) -> None:
        import os
        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUTF8"] = "1"
        self.proc = subprocess.Popen(
            self.command_args,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,
            env=env,
        )
        self.running = True
        threading.Thread(target=self._stdout_reader, daemon=True).start()
        threading.Thread(target=self._stderr_reader, daemon=True).start()

    def _stdout_reader(self) -> None:
        if not self.proc or not self.proc.stdout:
            return
        while self.running and self.proc:
            raw_bytes = self.proc.stdout.readline()
            if not raw_bytes:
                break
            line = raw_bytes.decode("utf-8", errors="replace").strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
                req_id = msg.get("id")
                with self._lock:
                    q = self._pending.get(req_id)
                if q:
                    q.put(msg)
            except Exception:
                pass

    def _stderr_reader(self) -> None:
        if not self.proc or not self.proc.stderr:
            return
        for raw_bytes in self.proc.stderr:
            if not raw_bytes:
                break
            line = raw_bytes.decode("utf-8", errors="replace")
            sys.stderr.write(f"[Server Log] {line}")
            sys.stderr.flush()

    def send_request(self, method: str, params: Dict[str, Any], req_id: Any, timeout: float = 65.0) -> Optional[Dict[str, Any]]:
        """Sends a JSON-RPC request and synchronously awaits the response."""
        resp_q: queue.Queue = queue.Queue()
        with self._lock:
            self._pending[req_id] = resp_q

        msg = {"jsonrpc": "2.0", "id": req_id, "method": method, "params": params}
        raw_line = json.dumps(msg, ensure_ascii=False) + "\n"

        if self.proc and self.proc.stdin:
            self.proc.stdin.write(raw_line.encode("utf-8"))
            self.proc.stdin.flush()

        try:
            return resp_q.get(timeout=timeout)
        except queue.Empty:
            return None
        finally:
            with self._lock:
                self._pending.pop(req_id, None)

    def close(self) -> None:
        self.running = False
        if self.proc:
            try:
                self.proc.terminate()
            except Exception:
                pass


class MockWebClient:
    """Simulates Claude Web connecting over HTTP/SSE."""

    def __init__(self, sse_url: str = "http://127.0.0.1:8765/sse"):
        self.sse_url = sse_url
        self.endpoint_url: Optional[str] = None
        self._responses: Dict[Any, queue.Queue] = {}
        self._lock = threading.Lock()
        self.connected = False

    def connect(self) -> None:
        """Connects to the SSE stream and listens for events."""
        threading.Thread(target=self._stream_listener, daemon=True).start()
        # Wait for endpoint event
        start = time.time()
        while not self.endpoint_url and (time.time() - start < 5.0):
            time.sleep(0.1)

    def _stream_listener(self) -> None:
        try:
            req = urllib.request.Request(self.sse_url)
            with urllib.request.urlopen(req, timeout=60) as resp:
                self.connected = True
                curr_event = None
                for line_bytes in resp:
                    line = line_bytes.decode("utf-8").strip()
                    if line.startswith("event:"):
                        curr_event = line.replace("event:", "").strip()
                    elif line.startswith("data:"):
                        data_str = line.replace("data:", "").strip()
                        if curr_event == "endpoint":
                            base = "/".join(self.sse_url.split("/")[:3])
                            self.endpoint_url = f"{base}{data_str}"
                        elif curr_event == "message":
                            try:
                                msg = json.loads(data_str)
                                req_id = msg.get("id")
                                with self._lock:
                                    q = self._responses.get(req_id)
                                if q:
                                    q.put(msg)
                            except Exception:
                                pass
        except Exception as e:
            logger.debug(f"MockWebClient stream ended: {e}")

    def send_tool_call(self, tool_name: str, arguments: Dict[str, Any], req_id: Any, timeout: float = 65.0) -> Optional[Dict[str, Any]]:
        """Sends a tool call POST request to the SSE endpoint."""
        if not self.endpoint_url:
            raise RuntimeError("Not connected to SSE gateway.")

        resp_q: queue.Queue = queue.Queue()
        with self._lock:
            self._responses[req_id] = resp_q

        payload = {
            "jsonrpc": "2.0",
            "id": req_id,
            "method": "tools/call",
            "params": {"name": tool_name, "arguments": arguments},
        }
        data_bytes = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            self.endpoint_url,
            data=data_bytes,
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        with urllib.request.urlopen(req, timeout=10) as http_resp:
            pass

        try:
            return resp_q.get(timeout=timeout)
        except queue.Empty:
            return None
        finally:
            with self._lock:
                self._responses.pop(req_id, None)
