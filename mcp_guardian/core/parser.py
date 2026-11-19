"""
JSON-RPC 2.0 & Model Context Protocol (MCP) Message Parser.
Handles message framing, message classification, and synthetic response generation.
"""

from __future__ import annotations

import json
from typing import Any, Dict, Optional, Tuple
from mcp_guardian.core.models import ClientType, MCPRequest
from mcp_guardian.utils.risk_analyzer import analyze_risk


class MCPParser:
    """Parses and serializes MCP JSON-RPC 2.0 messages."""

    INTERCEPTABLE_METHODS = {"tools/call", "resources/read"}

    @staticmethod
    def parse_line(line: str) -> Optional[Dict[str, Any]]:
        """Parses a raw string line into a JSON dictionary."""
        cleaned = line.strip()
        if not cleaned:
            return None
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            return None

    @classmethod
    def is_interceptable(cls, message: Dict[str, Any]) -> bool:
        """Determines if a message represents an action requiring user confirmation."""
        if not isinstance(message, dict):
            return False
        # Must have an ID and an interceptable method
        if "id" not in message:
            return False
        method = message.get("method")
        return method in cls.INTERCEPTABLE_METHODS

    @classmethod
    def extract_request(
        cls,
        message: Dict[str, Any],
        server_name: str = "default",
        client_type: ClientType = ClientType.UNKNOWN,
    ) -> Optional[MCPRequest]:
        """Extracts an MCPRequest object from an incoming JSON-RPC dictionary."""
        if not cls.is_interceptable(message):
            return None

        req_id = message.get("id")
        method = message.get("method", "")
        params = message.get("params", {}) or {}

        tool_name = None
        arguments = {}

        if method == "tools/call":
            tool_name = params.get("name", "unknown_tool")
            arguments = params.get("arguments", {}) or {}
        elif method == "resources/read":
            tool_name = params.get("uri", "unknown_resource")
            arguments = params

        risk_level, risk_reasons = analyze_risk(tool_name or "", arguments)

        return MCPRequest(
            id=req_id,
            method=method,
            server_name=server_name,
            tool_name=tool_name,
            arguments=arguments,
            client_type=client_type,
            raw_json=message,
            risk_level=risk_level,
            risk_reasons=risk_reasons,
        )

    @staticmethod
    def make_blocked_tool_response(
        req_id: Any,
        tool_name: Optional[str] = None,
        reason: str = "Execution blocked by user in confirmation popup",
    ) -> Dict[str, Any]:
        """
        Creates an MCP-compliant tool error response.
        Returning an MCP result with isError=True is the cleanest way for Claude
        to understand that the tool was denied by the user without terminating the chat session.
        """
        tool_display = f"'{tool_name}'" if tool_name else "requested action"
        denial_message = (
            f"[MCP Guardian 🛡️] Execution of {tool_display} was BLOCKED by user.\n"
            f"Reason: {reason}"
        )
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "content": [
                    {
                        "type": "text",
                        "text": denial_message,
                    }
                ],
                "isError": True,
            },
        }

    @staticmethod
    def make_jsonrpc_error_response(
        req_id: Any,
        code: int = -32000,
        message: str = "Request denied by operator policy",
    ) -> Dict[str, Any]:
        """Creates a standard JSON-RPC 2.0 error response."""
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "error": {
                "code": code,
                "message": message,
            },
        }

    @staticmethod
    def serialize(message: Dict[str, Any]) -> str:
        """Serializes a dictionary to a JSON line."""
        return json.dumps(message, ensure_ascii=False) + "\n"
