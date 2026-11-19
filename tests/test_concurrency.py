"""Concurrency & parallel tool call tests for ConcurrencyManager."""

import concurrent.futures
import sys
import time
from pathlib import Path

# Add project root to path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from mcp_guardian.core.models import Decision, DecisionAction, MCPRequest
from mcp_guardian.core.manager import ConcurrencyManager


def test_concurrent_requests():
    manager = ConcurrencyManager()
    results = {}

    def simulate_client(req_id: int):
        req = MCPRequest(
            id=req_id,
            method="tools/call",
            server_name="concurrent_srv",
            tool_name=f"tool_{req_id}",
            arguments={"val": req_id * 10},
        )
        dec = manager.request_decision(req, timeout=5.0)
        results[req_id] = dec
        return req_id, dec

    def mock_operator_decider():
        # As requests get registered, resolve them with alternating Allow / Block
        time.sleep(0.05)
        for _ in range(50):
            pending = manager.get_pending_requests()
            for p in pending:
                action = DecisionAction.ALLOW if (p.id % 2 == 0) else DecisionAction.BLOCK
                dec = Decision(request_id=p.id, action=action, reason=f"Resolved concurrently {action.value}")
                manager.submit_decision(p.id, dec)
            time.sleep(0.05)

    import threading
    t_operator = threading.Thread(target=mock_operator_decider, daemon=True)
    t_operator.start()

    # Launch 10 parallel client requests
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        futures = [executor.submit(simulate_client, i) for i in range(1, 11)]
        concurrent.futures.wait(futures)

    assert len(results) == 10
    for req_id, dec in results.items():
        assert dec.request_id == req_id
        if req_id % 2 == 0:
            assert dec.action == DecisionAction.ALLOW
        else:
            assert dec.action == DecisionAction.BLOCK


if __name__ == "__main__":
    test_concurrent_requests()
    print("Concurrency test passed!")
