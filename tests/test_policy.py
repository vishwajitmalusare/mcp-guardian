"""Unit tests for PolicyEngine and ConcurrencyManager."""

import sys
import time
from pathlib import Path

# Add project root to path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from mcp_guardian.core.models import Decision, DecisionAction, MCPRequest, RiskLevel
from mcp_guardian.core.policy import PolicyEngine
from mcp_guardian.core.manager import ConcurrencyManager


def test_session_rules():
    policy = PolicyEngine()
    req = MCPRequest(
        id=1,
        method="tools/call",
        server_name="test_srv",
        tool_name="my_tool",
    )

    # Initially no cached decision
    assert policy.get_session_decision(req) is None

    # Remember ALLOW decision
    dec = Decision(
        request_id=1,
        action=DecisionAction.ALLOW,
        remember_session=True,
    )
    policy.record_decision(req, dec)

    # Next request for same tool should be auto-allowed
    req2 = MCPRequest(
        id=2,
        method="tools/call",
        server_name="test_srv",
        tool_name="my_tool",
    )
    cached = policy.get_session_decision(req2)
    assert cached is not None
    assert cached.action == DecisionAction.ALLOW
    assert cached.remember_session is True

    # Clear rules
    policy.clear_session_rules()
    assert policy.get_session_decision(req2) is None


def test_timeout_fallback():
    policy = PolicyEngine(default_timeout_seconds=0.1, fail_safe_action=DecisionAction.BLOCK)
    req = MCPRequest(id="timeout-test", method="tools/call", server_name="srv", tool_name="tool")
    dec = policy.get_timeout_decision(req)
    assert dec.action == DecisionAction.BLOCK
    assert "timed out" in dec.reason.lower()


def test_concurrency_manager_resolution():
    manager = ConcurrencyManager()
    req = MCPRequest(id=100, method="tools/call", server_name="srv", tool_name="tool")

    # Simulate answering on another thread
    import threading

    def answer():
        time.sleep(0.1)
        manager.submit_decision(
            100,
            Decision(request_id=100, action=DecisionAction.ALLOW, reason="Approved via test"),
        )

    threading.Thread(target=answer).start()
    result = manager.request_decision(req, timeout=2.0)
    assert result.action == DecisionAction.ALLOW
    assert result.reason == "Approved via test"


if __name__ == "__main__":
    test_session_rules()
    test_timeout_fallback()
    test_concurrency_manager_resolution()
    print("All policy and manager tests passed!")
