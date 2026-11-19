"""
Central Background Daemon Service for MCP Guardian.
Runs the IPC server and coordinates concurrency, policies, audit logging, and authorization popups.
"""

from __future__ import annotations

import logging
import signal
import sys
import threading
import time
from typing import Optional
from mcp_guardian.core.audit import AuditLogger, default_audit_logger
from mcp_guardian.core.manager import ConcurrencyManager
from mcp_guardian.core.models import Decision, MCPRequest
from mcp_guardian.core.policy import PolicyEngine
from mcp_guardian.daemon.ipc import IPCServer
from mcp_guardian.gui.popup import ask_user_confirmation_sync

logger = logging.getLogger("mcp_guardian.service")


class GuardianDaemonService:
    """Core daemon managing background authorization services."""

    def __init__(
        self,
        policy_engine: Optional[PolicyEngine] = None,
        audit_logger: Optional[AuditLogger] = None,
        ipc_port: int = 8766,
    ):
        self.policy = policy_engine or PolicyEngine()
        self.audit = audit_logger or default_audit_logger
        self.manager = ConcurrencyManager(policy_engine=self.policy)
        self.ipc_port = ipc_port
        self.ipc_server = IPCServer(
            request_handler=self.handle_authorization_request,
            port=self.ipc_port,
        )
        self.running = False

    def handle_authorization_request(self, request: MCPRequest) -> Decision:
        """Invoked via IPC when a wrapper requests authorization."""
        start_time = time.time()
        # Request decision through concurrency manager
        decision = self.manager.request_decision(request)
        duration_ms = (time.time() - start_time) * 1000.0

        # Record to audit trail
        from mcp_guardian.core.models import AuditEntry

        entry = AuditEntry(
            request=request,
            decision=decision,
            duration_ms=duration_ms,
            downstream_status="COMPLETED" if decision.is_allowed else "BLOCKED",
        )
        self.audit.record(entry)
        return decision

    def start(self, interactive_gui: bool = True) -> None:
        """Starts the daemon service."""
        self.running = True

        # Default handler if no dashboard UI is attached: spawn individual sync popup
        def default_gui_handler(req: MCPRequest):
            def ask_worker():
                dec = ask_user_confirmation_sync(req, self.policy.default_timeout_seconds)
                self.manager.submit_decision(req.id, dec)

            threading.Thread(target=ask_worker, daemon=True).start()

        self.manager.set_request_handler(default_gui_handler)
        self.ipc_server.start()
        logger.info(f"MCP Guardian Daemon started. IPC listening on port {self.ipc_port}")

    def stop(self) -> None:
        """Shuts down the daemon service."""
        self.running = False
        self.ipc_server.stop()
        self.manager.cancel_all_pending("Guardian daemon service stopped")
        logger.info("MCP Guardian Daemon stopped.")
