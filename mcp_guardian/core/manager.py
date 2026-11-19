"""
Pending Request & Concurrency Manager for MCP Guardian.
Coordinates concurrent in-flight requests, synchronization events, timeouts, and decision resolution.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Callable, Dict, List, Optional
from mcp_guardian.core.models import Decision, DecisionAction, MCPRequest
from mcp_guardian.core.policy import PolicyEngine


logger = logging.getLogger("mcp_guardian.manager")


class PendingRequestItem:
    """Wrapper holding a pending MCP request and its completion synchronization event."""

    def __init__(self, request: MCPRequest):
        self.request = request
        self.event = threading.Event()
        self.decision: Optional[Decision] = None
        self.created_at = time.time()


class ConcurrencyManager:
    """Thread-safe coordinator for concurrent MCP request authorizations."""

    def __init__(
        self,
        policy_engine: Optional[PolicyEngine] = None,
        on_request_queued: Optional[Callable[[MCPRequest], None]] = None,
    ):
        self.policy = policy_engine or PolicyEngine()
        self._on_request_queued = on_request_queued
        self._lock = threading.Lock()
        self._pending: Dict[Any, PendingRequestItem] = {}

    def set_request_handler(self, callback: Callable[[MCPRequest], None]) -> None:
        """Sets the callback to be invoked when a new request needs human confirmation."""
        self._on_request_queued = callback

    def request_decision(self, request: MCPRequest, timeout: Optional[float] = None) -> Decision:
        """
        Blocks the calling thread until a decision is rendered for the request or timeout occurs.
        Handles concurrent requests safely by scoping events per request.id.
        """
        # 1. Check session policy memory
        cached_decision = self.policy.get_session_decision(request)
        if cached_decision is not None:
            logger.info(f"Auto-decision from session policy for {request.display_name}: {cached_decision.action.value}")
            return cached_decision

        # 2. Register pending request
        item = PendingRequestItem(request)
        with self._lock:
            self._pending[request.id] = item

        # 3. Notify GUI / IPC to show confirmation popup
        if self._on_request_queued:
            try:
                self._on_request_queued(request)
            except Exception as e:
                logger.error(f"Error notifying request queued callback: {e}")

        # 4. Wait for user decision with timeout
        wait_time = timeout if timeout is not None else self.policy.default_timeout_seconds
        signaled = item.event.wait(timeout=wait_time)

        # 5. Extract decision or fallback to timeout
        with self._lock:
            self._pending.pop(request.id, None)

        if signaled and item.decision is not None:
            decision = item.decision
            self.policy.record_decision(request, decision)
            return decision

        # Timed out
        timeout_decision = self.policy.get_timeout_decision(request)
        logger.warning(f"Request {request.id} timed out. Defaulting to: {timeout_decision.action.value}")
        return timeout_decision

    def submit_decision(self, request_id: Any, decision: Decision) -> bool:
        """
        Submits a human decision (Allow / Block) for a pending request ID.
        Releases the waiting thread. Returns True if request was pending, False otherwise.
        """
        with self._lock:
            item = self._pending.get(request_id)
            if not item:
                return False
            item.decision = decision
            item.event.set()
            return True

    def get_pending_requests(self) -> List[MCPRequest]:
        """Returns a snapshot of all currently pending requests."""
        with self._lock:
            return [item.request for item in self._pending.values()]

    def cancel_all_pending(self, reason: str = "Interception service terminated") -> None:
        """Cancels all pending requests, immediately rejecting them."""
        with self._lock:
            pending_items = list(self._pending.values())
            self._pending.clear()

        for item in pending_items:
            item.decision = Decision(
                request_id=item.request.id,
                action=DecisionAction.BLOCK,
                reason=reason,
                timestamp=time.time(),
            )
            item.event.set()
