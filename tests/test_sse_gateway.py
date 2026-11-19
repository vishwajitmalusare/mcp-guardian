"""Integration and unit tests for SSEGatewayServer & MCPGatewayHandler."""

import io
import json
import sys
import time
from pathlib import Path

# Add project root
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from mcp_guardian.core.models import Decision, DecisionAction, MCPRequest
from mcp_guardian.core.parser import MCPParser
from mcp_guardian.transports.sse_gateway import MCPGatewayHandler, SSESession, DownstreamStdioBridge


class MockClientStream:
    """Simulates an in-memory HTTP stream for testing MCPGatewayHandler without network sockets."""

    def __init__(self, method: str, path: str, body: bytes = b""):
        self.rfile = io.BytesIO(body)
        self.wfile = io.BytesIO()
        self.method = method
        self.path = path
        self.headers = {
            "Content-Length": str(len(body)),
            "Content-Type": "application/json",
        }


def test_sse_session_management():
    session = SSESession("test-session-1")
    assert session.is_active is True

    # Push message
    msg = {"jsonrpc": "2.0", "id": 1, "result": {"test": "ok"}}
    session.push(msg)

    popped = session.message_queue.get(timeout=1.0)
    assert popped == msg

    session.close()
    assert session.is_active is False


def test_sse_gateway_authorization_allow_and_block():
    # Setup test policy handler
    decisions_rendered = []

    def mock_policy_func(req: MCPRequest) -> Decision:
        if "rm -rf" in str(req.arguments) or "danger" in (req.tool_name or ""):
            dec = Decision(request_id=req.id, action=DecisionAction.BLOCK, reason="Denied dangerous web tool")
        else:
            dec = Decision(request_id=req.id, action=DecisionAction.ALLOW, reason="Approved safe web tool")
        decisions_rendered.append(dec)
        return dec

    # Mock handler
    MCPGatewayHandler._authorize_request = lambda self, req: mock_policy_func(req)
    session = SSESession("session-web-test")
    MCPGatewayHandler.sessions["session-web-test"] = session

    # 1. Test Safe Tool Call (Allowed)
    safe_call = {
        "jsonrpc": "2.0",
        "id": "call-1",
        "method": "tools/call",
        "params": {
            "name": "calculate_sum",
            "arguments": {"a": 50, "b": 75},
        },
    }

    handler_dummy = MCPGatewayHandler.__new__(MCPGatewayHandler)
    handler_dummy._process_mcp_message(safe_call, session)

    # Response should be in session queue
    res_allowed = session.message_queue.get(timeout=2.0)
    assert res_allowed["id"] == "call-1"
    assert res_allowed["result"]["isError"] is False
    assert len(decisions_rendered) == 1
    assert decisions_rendered[0].is_allowed is True

    # 2. Test Dangerous Tool Call (Blocked)
    danger_call = {
        "jsonrpc": "2.0",
        "id": "call-2",
        "method": "tools/call",
        "params": {
            "name": "execute_command",
            "arguments": {"command": "rm -rf /"},
        },
    }

    handler_dummy._process_mcp_message(danger_call, session)

    res_blocked = session.message_queue.get(timeout=2.0)
    assert res_blocked["id"] == "call-2"
    assert res_blocked["result"]["isError"] is True
    denial_text = res_blocked["result"]["content"][0]["text"]
    assert "BLOCKED by user" in denial_text
    assert "Denied dangerous web tool" in denial_text
    assert len(decisions_rendered) == 2
    assert decisions_rendered[1].is_blocked is True

    session.close()


def test_downstream_stdio_bridge():
    sample_cmd = [sys.executable, "-m", "mcp_guardian.mock.sample_mcp_server"]
    bridge = DownstreamStdioBridge(sample_cmd)
    bridge.start()
    time.sleep(1.0)

    try:
        # Handshake
        init_res = bridge.execute({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}, timeout=5.0)
        assert init_res is not None
        assert "serverInfo" in init_res.get("result", {})

        # Tool execution
        tool_res = bridge.execute(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": "calculate_sum", "arguments": {"a": 12, "b": 13}},
            },
            timeout=5.0,
        )
        assert tool_res is not None
        assert "25" in tool_res["result"]["content"][0]["text"]
    finally:
        bridge.stop()


if __name__ == "__main__":
    test_sse_session_management()
    test_sse_gateway_authorization_allow_and_block()
    test_downstream_stdio_bridge()
    print("All SSE Gateway and Bridge tests passed!")
