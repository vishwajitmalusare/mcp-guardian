"""
Heuristic Risk Analyzer for MCP Tool Calls.
Analyzes tool names and arguments to identify potentially destructive or dangerous operations.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Tuple
from mcp_guardian.core.models import RiskLevel


# Dangerous command patterns
CRITICAL_COMMAND_PATTERNS = [
    r"rm\s+-(?:r|f|rf|fr)\b",
    r"del\s+/[fFqsS]",
    r"rd\s+/[sS]",
    r"format\s+[a-zA-Z]:",
    r"mkfs\.",
    r"dd\s+if=",
    r":\(\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;",  # Fork bomb
    r"chmod\s+-R\s+777",
    r"curl\b.*\|\s*(?:ba)?sh",
    r"wget\b.*\|\s*(?:ba)?sh",
    r">\s*/dev/sd[a-z]",
    r"DROP\s+(?:TABLE|DATABASE|SCHEMA)\b",
    r"TRUNCATE\s+TABLE\b",
]

SENSITIVE_FILE_PATTERNS = [
    r"\.env(?:\..*)?$",
    r"id_rsa",
    r"id_ed25519",
    r"credentials(?:\.json)?$",
    r"\.aws[/\\]",
    r"\.ssh[/\\]",
    r"shadow$",
    r"passwd$",
    r"SAM$",
    r"SYSTEM$",
]

EXEC_TOOLS = {"execute_command", "run_command", "bash", "shell", "terminal", "exec", "cmd", "run_terminal_command"}
WRITE_TOOLS = {"write_file", "edit_file", "create_file", "save_file", "overwrite_file", "update_file"}
DELETE_TOOLS = {"delete_file", "remove_file", "drop_table", "truncate_table", "delete_directory"}
READ_TOOLS = {"read_file", "view_file", "get_file", "cat"}


def analyze_risk(tool_name: str, arguments: Dict[str, Any]) -> Tuple[RiskLevel, List[str]]:
    """
    Evaluates the risk level and provides human-readable explanations.
    Returns (RiskLevel, List[str]).
    """
    reasons: List[str] = []
    tool_lower = (tool_name or "").lower()
    arg_str = str(arguments)

    # 1. Check for command execution
    if any(t in tool_lower for t in EXEC_TOOLS):
        reasons.append("Arbitrary command execution capability")
        cmd = ""
        for k in ("command", "cmd", "script", "code"):
            if k in arguments and isinstance(arguments[k], str):
                cmd += " " + arguments[k]

        # Check critical patterns in command
        for pat in CRITICAL_COMMAND_PATTERNS:
            if re.search(pat, cmd, re.IGNORECASE):
                reasons.append(f"Critical destructive pattern detected in command: {pat}")
                return RiskLevel.CRITICAL, reasons

        # If not critical, standard command execution is still High risk
        return RiskLevel.HIGH, reasons

    # 2. Check for database destructive queries
    for pat in [r"DROP\s+", r"TRUNCATE\s+", r"DELETE\s+FROM"]:
        if re.search(pat, arg_str, re.IGNORECASE):
            reasons.append("Destructive database query detected")
            return RiskLevel.CRITICAL, reasons

    # 3. Check for file deletion
    if any(t in tool_lower for t in DELETE_TOOLS):
        reasons.append("File or resource deletion operation")
        return RiskLevel.HIGH, reasons

    # 4. Check for sensitive file accesses
    for pat in SENSITIVE_FILE_PATTERNS:
        if re.search(pat, arg_str, re.IGNORECASE):
            reasons.append("Accesses sensitive credentials, keys, or environment files")
            return RiskLevel.HIGH, reasons

    # 5. Check for file writing/modification
    if any(t in tool_lower for t in WRITE_TOOLS):
        reasons.append("File modification/creation operation")
        return RiskLevel.MEDIUM, reasons

    # 6. Check for file reading
    if any(t in tool_lower for t in READ_TOOLS):
        reasons.append("File read operation")
        return RiskLevel.LOW, reasons

    # 7. Safe tools
    return RiskLevel.SAFE, ["Standard read-only or computational query"]
