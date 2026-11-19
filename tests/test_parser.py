"""Unit tests for MCPParser and RiskAnalyzer."""

import sys
from pathlib import Path

# Add project root to path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from mcp_guardian.core.models import ClientType, RiskLevel
from mcp_guardian.core.parser import MCPParser
from mcp_guardian.utils.risk_analyzer import analyze_risk


def test_parse_valid_json_line():
    line = '{"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "test"}}\n'
    parsed = MCPParser.parse_line(line)
    assert parsed is not None
    assert parsed["id"] == 1
    assert parsed["method"] == "tools/call"


def test_parse_invalid_json():
    assert MCPParser.parse_line("not a json\n") is None
    assert MCPParser.parse_line("") is None
    assert MCPParser.parse_line("   ") is None


def test_is_interceptable():
    # Tools call is interceptable
    assert MCPParser.is_interceptable({"jsonrpc": "2.0", "id": 1, "method": "tools/call"})
    # Resources read is interceptable
    assert MCPParser.is_interceptable({"jsonrpc": "2.0", "id": 2, "method": "resources/read"})
    # Discovery methods are NOT interceptable (transparent)
    assert not MCPParser.is_interceptable({"jsonrpc": "2.0", "id": 3, "method": "initialize"})
    assert not MCPParser.is_interceptable({"jsonrpc": "2.0", "id": 4, "method": "tools/list"})
    # Notifications (no id) are NOT interceptable
    assert not MCPParser.is_interceptable({"jsonrpc": "2.0", "method": "notifications/initialized"})


def test_extract_request():
    msg = {
        "jsonrpc": "2.0",
        "id": "req-99",
        "method": "tools/call",
        "params": {
            "name": "calculate_sum",
            "arguments": {"a": 10, "b": 20},
        },
    }
    req = MCPParser.extract_request(msg, server_name="math_srv", client_type=ClientType.CLAUDE_DESKTOP)
    assert req is not None
    assert req.id == "req-99"
    assert req.server_name == "math_srv"
    assert req.tool_name == "calculate_sum"
    assert req.arguments == {"a": 10, "b": 20}
    assert req.client_type == ClientType.CLAUDE_DESKTOP
    assert req.risk_level == RiskLevel.SAFE


def test_risk_analysis_dangerous_commands():
    # Critical command
    risk, reasons = analyze_risk("execute_command", {"command": "rm -rf /"})
    assert risk == RiskLevel.CRITICAL
    assert any("destructive" in r.lower() for r in reasons)

    # High risk general command
    risk_high, _ = analyze_risk("bash", {"command": "echo Hello"})
    assert risk_high == RiskLevel.HIGH

    # Database drop
    risk_db, _ = analyze_risk("sql_query", {"query": "DROP TABLE users;"})
    assert risk_db == RiskLevel.CRITICAL

    # Safe tool
    risk_safe, _ = analyze_risk("calculate_sum", {"a": 1, "b": 2})
    assert risk_safe == RiskLevel.SAFE


def test_make_blocked_tool_response():
    resp = MCPParser.make_blocked_tool_response(
        req_id=42,
        tool_name="delete_database",
        reason="Blocked by administrator",
    )
    assert resp["jsonrpc"] == "2.0"
    assert resp["id"] == 42
    assert resp["result"]["isError"] is True
    assert "delete_database" in resp["result"]["content"][0]["text"]
    assert "Blocked by administrator" in resp["result"]["content"][0]["text"]


if __name__ == "__main__":
    test_parse_valid_json_line()
    test_parse_invalid_json()
    test_is_interceptable()
    test_extract_request()
    test_risk_analysis_dangerous_commands()
    test_make_blocked_tool_response()
    print("All parser and risk analyzer tests passed!")
