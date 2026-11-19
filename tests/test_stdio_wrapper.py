"""Integration tests for StdioWrapper."""

import json
import sys
import time
from pathlib import Path

# Add project root
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from mcp_guardian.core.models import Decision, DecisionAction
from mcp_guardian.mock.mock_client import MockStdioClient
from mcp_guardian.daemon.service import GuardianDaemonService


def test_stdio_wrapper_with_daemon():
    # 1. Start daemon on dedicated port
    daemon_port = 8799
    import os
    os.environ["MCP_GUARDIAN_IPC_PORT"] = str(daemon_port)
    daemon = GuardianDaemonService(ipc_port=daemon_port)

    # Automatically decide based on tool name for non-interactive test
    def automated_handler(req):
        if "danger" in (req.tool_name or "") or req.arguments.get("command"):
            # Block destructive tool
            dec = Decision(request_id=req.id, action=DecisionAction.BLOCK, reason="Auto-blocked test dangerous action")
        else:
            # Allow safe tool
            dec = Decision(request_id=req.id, action=DecisionAction.ALLOW, reason="Auto-allowed test safe action")
        daemon.manager.submit_decision(req.id, dec)

    daemon.manager.set_request_handler(automated_handler)
    daemon.ipc_server.port = daemon_port
    daemon.ipc_server.start()
    daemon.running = True

    try:
        # 2. Launch Stdio Wrapper pointing to IPC daemon
        wrapper_cmd = [
            sys.executable,
            "-m",
            "mcp_guardian",
            "wrap",
            "--server-name",
            "test_server",
            "--ipc-port",
            str(daemon_port),
            "--",
            sys.executable,
            "-m",
            "mcp_guardian.mock.sample_mcp_server",
        ]



        client = MockStdioClient(wrapper_cmd)
        client.start()
        time.sleep(1.0)

        # Step A: Initialize (transparent pass-through)
        init_res = client.send_request("initialize", {"protocolVersion": "2024-11-05"}, req_id=1, timeout=5.0)
        assert init_res is not None
        assert "serverInfo" in init_res.get("result", {})

        # Step B: Safe tool call (Allowed by policy)
        safe_res = client.send_request(
            "tools/call",
            {"name": "calculate_sum", "arguments": {"a": 20, "b": 30}},
            req_id=2,
            timeout=5.0,
        )
        assert safe_res is not None
        assert safe_res.get("result", {}).get("isError") is False
        content_text = safe_res["result"]["content"][0]["text"]
        assert "50" in content_text

        # Step C: Dangerous tool call (Blocked by policy)
        danger_res = client.send_request(
            "tools/call",
            {"name": "execute_command", "arguments": {"command": "rm -rf /"}},
            req_id=3,
            timeout=5.0,
        )
        assert danger_res is not None
        # Must be blocked: isError is True
        assert danger_res.get("result", {}).get("isError") is True
        denial_text = danger_res["result"]["content"][0]["text"]
        assert "BLOCKED by user" in denial_text
        assert "Auto-blocked test dangerous action" in denial_text

        client.close()
    finally:
        daemon.stop()


if __name__ == "__main__":
    test_stdio_wrapper_with_daemon()
    print("Stdio Wrapper integration test passed!")
