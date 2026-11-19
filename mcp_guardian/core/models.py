"""
Core Data Models for MCP Guardian.
Defines structured representations of MCP requests, responses, decisions, risk levels, and audit events.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class ClientType(str, Enum):
    CLAUDE_DESKTOP = "Claude Desktop"
    CLAUDE_WEB = "Claude Web"
    TEST_HARNESS = "Test Harness"
    UNKNOWN = "Unknown Client"


class RiskLevel(str, Enum):
    SAFE = "SAFE"
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class DecisionAction(str, Enum):
    ALLOW = "ALLOW"
    BLOCK = "BLOCK"


@dataclass
class MCPRequest:
    """Represents an intercepted Model Context Protocol request."""
    id: Any
    method: str
    server_name: str
    tool_name: Optional[str] = None
    arguments: Dict[str, Any] = field(default_factory=dict)
    client_type: ClientType = ClientType.UNKNOWN
    timestamp: float = field(default_factory=time.time)
    raw_json: Dict[str, Any] = field(default_factory=dict)
    risk_level: RiskLevel = RiskLevel.LOW
    risk_reasons: List[str] = field(default_factory=list)

    @property
    def is_tool_call(self) -> bool:
        return self.method == "tools/call"

    @property
    def display_name(self) -> str:
        if self.tool_name:
            return f"{self.server_name} -> {self.tool_name}"
        return f"{self.server_name} -> {self.method}"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "method": self.method,
            "server_name": self.server_name,
            "tool_name": self.tool_name,
            "arguments": self.arguments,
            "client_type": self.client_type.value if isinstance(self.client_type, ClientType) else str(self.client_type),
            "timestamp": self.timestamp,
            "risk_level": self.risk_level.value if isinstance(self.risk_level, RiskLevel) else str(self.risk_level),
            "risk_reasons": self.risk_reasons,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> MCPRequest:
        client_type = ClientType(data.get("client_type", ClientType.UNKNOWN))
        risk_level = RiskLevel(data.get("risk_level", RiskLevel.LOW))
        return cls(
            id=data.get("id"),
            method=data.get("method", ""),
            server_name=data.get("server_name", "unknown"),
            tool_name=data.get("tool_name"),
            arguments=data.get("arguments", {}),
            client_type=client_type,
            timestamp=data.get("timestamp", time.time()),
            raw_json=data.get("raw_json", {}),
            risk_level=risk_level,
            risk_reasons=data.get("risk_reasons", []),
        )


@dataclass
class Decision:
    """Represents a human decision (Allow / Block) for an MCP request."""
    request_id: Any
    action: DecisionAction
    reason: str = ""
    remember_session: bool = False
    timestamp: float = field(default_factory=time.time)

    @property
    def is_allowed(self) -> bool:
        return self.action == DecisionAction.ALLOW

    @property
    def is_blocked(self) -> bool:
        return self.action == DecisionAction.BLOCK

    def to_dict(self) -> Dict[str, Any]:
        return {
            "request_id": self.request_id,
            "action": self.action.value if isinstance(self.action, DecisionAction) else str(self.action),
            "reason": self.reason,
            "remember_session": self.remember_session,
            "timestamp": self.timestamp,
        }


@dataclass
class AuditEntry:
    """Comprehensive log record of an intercepted request and its decision."""
    request: MCPRequest
    decision: Decision
    duration_ms: float = 0.0
    downstream_status: str = "COMPLETED"  # "COMPLETED", "BLOCKED", "ERROR", "TIMEOUT"
    downstream_error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "timestamp": self.request.timestamp,
            "request": self.request.to_dict(),
            "decision": self.decision.to_dict(),
            "duration_ms": round(self.duration_ms, 2),
            "downstream_status": self.downstream_status,
            "downstream_error": self.downstream_error,
        }
