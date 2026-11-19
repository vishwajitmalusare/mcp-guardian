"""
Policy Engine for MCP Guardian.
Maintains session rules, default timeouts, and evaluates automated decisions.
"""

from __future__ import annotations

import threading
import time
from typing import Dict, Optional, Tuple
from mcp_guardian.core.models import Decision, DecisionAction, MCPRequest, RiskLevel


class PolicyEngine:
    """Manages access control rules and session authorization memory."""

    def __init__(self, default_timeout_seconds: float = 60.0, fail_safe_action: DecisionAction = DecisionAction.BLOCK):
        self.default_timeout_seconds = default_timeout_seconds
        self.fail_safe_action = fail_safe_action
        self._lock = threading.Lock()
        # Session remembered rules: (server_name, tool_name) -> DecisionAction
        self._session_rules: Dict[Tuple[str, str], DecisionAction] = {}
        # Auto-allow safe tools? (Default: False - require human confirmation for maximum safety)
        self.auto_allow_safe: bool = False

    def get_session_decision(self, request: MCPRequest) -> Optional[Decision]:
        """Checks if a decision has already been remembered for this tool in the current session."""
        with self._lock:
            key = (request.server_name, request.tool_name or "")
            if key in self._session_rules:
                action = self._session_rules[key]
                return Decision(
                    request_id=request.id,
                    action=action,
                    reason=f"Auto-applied from session rule ({action.value})",
                    remember_session=True,
                    timestamp=time.time(),
                )

        if self.auto_allow_safe and request.risk_level == RiskLevel.SAFE:
            return Decision(
                request_id=request.id,
                action=DecisionAction.ALLOW,
                reason="Auto-allowed by policy (SAFE risk level)",
                remember_session=False,
                timestamp=time.time(),
            )

        return None

    def record_decision(self, request: MCPRequest, decision: Decision) -> None:
        """Stores a decision into session memory if requested."""
        if decision.remember_session and request.tool_name:
            with self._lock:
                key = (request.server_name, request.tool_name)
                self._session_rules[key] = decision.action

    def clear_session_rules(self) -> None:
        """Clears all session-remembered decisions."""
        with self._lock:
            self._session_rules.clear()

    def get_timeout_decision(self, request: MCPRequest) -> Decision:
        """Creates a fail-safe decision if a request times out waiting for human confirmation."""
        return Decision(
            request_id=request.id,
            action=self.fail_safe_action,
            reason=f"Confirmation timed out after {self.default_timeout_seconds}s (Fail-safe default: {self.fail_safe_action.value})",
            remember_session=False,
            timestamp=time.time(),
        )
