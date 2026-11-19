"""
Audit Logger for MCP Guardian.
Maintains persistent, structured audit trails and in-memory stats for all intercepted communications.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import threading
import time
from collections import deque
from pathlib import Path
from typing import Callable, Deque, Dict, List, Optional
from mcp_guardian.core.models import AuditEntry, DecisionAction


class AuditLogger:
    """Thread-safe persistent audit logger and metrics tracker."""

    def __init__(self, log_dir: Optional[str] = None, max_in_memory: int = 500):
        if log_dir is None:
            log_dir = os.environ.get("MCP_GUARDIAN_LOG_DIR")
        if log_dir is None:
            # Prefer local workspace directory (.mcp_guardian) first
            try:
                candidate = Path.cwd() / ".mcp_guardian"
                candidate.mkdir(parents=True, exist_ok=True)
                log_dir = str(candidate)
            except Exception:
                import tempfile
                log_dir = os.path.join(tempfile.gettempdir(), "mcp_guardian")

        self.log_dir = Path(log_dir)
        try:
            self.log_dir.mkdir(parents=True, exist_ok=True)
        except Exception:
            import tempfile
            self.log_dir = Path(tempfile.gettempdir()) / "mcp_guardian"
            self.log_dir.mkdir(parents=True, exist_ok=True)

        self.jsonl_path = self.log_dir / "audit_trail.jsonl"
        self.text_log_path = self.log_dir / "mcp_guardian.log"

        self._lock = threading.Lock()
        self._entries: Deque[AuditEntry] = deque(maxlen=max_in_memory)
        self._subscribers: List[Callable[[AuditEntry], None]] = []

        # Metrics
        self.total_requests = 0
        self.allowed_requests = 0
        self.blocked_requests = 0

        # Configure file logger safely
        self._logger = logging.getLogger("mcp_guardian.audit")
        self._logger.setLevel(logging.INFO)
        if not self._logger.handlers:
            try:
                handler = logging.FileHandler(str(self.text_log_path), encoding="utf-8")
                formatter = logging.Formatter("[%(asctime)s] [%(levelname)s] %(message)s")
                handler.setFormatter(formatter)
                self._logger.addHandler(handler)
            except Exception:
                # Fallback to stream handler if file cannot be opened
                handler = logging.StreamHandler(sys.stderr)
                formatter = logging.Formatter("[%(asctime)s] [%(levelname)s] %(message)s")
                handler.setFormatter(formatter)
                self._logger.addHandler(handler)

    def subscribe(self, callback: Callable[[AuditEntry], None]) -> None:
        """Subscribes a listener (e.g., UI dashboard) to real-time audit events."""
        with self._lock:
            self._subscribers.append(callback)

    def record(self, entry: AuditEntry) -> None:
        """Records an audit entry persistently and notifies listeners."""
        with self._lock:
            self._entries.append(entry)
            self.total_requests += 1
            if entry.decision.action == DecisionAction.ALLOW:
                self.allowed_requests += 1
            else:
                self.blocked_requests += 1

            # Append to structured JSONL file
            try:
                with open(self.jsonl_path, "a", encoding="utf-8") as f:
                    f.write(json.dumps(entry.to_dict(), ensure_ascii=False) + "\n")
            except Exception as e:
                self._logger.error(f"Failed to write to JSONL log: {e}")

            # Write human-readable log line
            status_symbol = "ALLOWED ✅" if entry.decision.is_allowed else "BLOCKED ❌"
            log_msg = (
                f"{status_symbol} | Client: {entry.request.client_type.value} | "
                f"Server: {entry.request.server_name} | Tool: {entry.request.tool_name} | "
                f"ID: {entry.request.id} | Latency: {entry.duration_ms:.1f}ms | "
                f"Reason: {entry.decision.reason}"
            )
            self._logger.info(log_msg)
            subscribers_copy = list(self._subscribers)

        # Notify outside lock to avoid deadlocks
        for sub in subscribers_copy:
            try:
                sub(entry)
            except Exception:
                pass

    def get_recent(self, limit: int = 50) -> List[AuditEntry]:
        """Returns the most recent audit entries."""
        with self._lock:
            entries = list(self._entries)
        return entries[-limit:]

    def get_stats(self) -> Dict[str, Any]:
        """Returns current operational statistics."""
        with self._lock:
            total = self.total_requests
            allowed = self.allowed_requests
            blocked = self.blocked_requests
            block_rate = (blocked / total * 100.0) if total > 0 else 0.0

            return {
                "total": total,
                "allowed": allowed,
                "blocked": blocked,
                "block_rate_percent": round(block_rate, 1),
            }


# Global default audit logger instance
default_audit_logger = AuditLogger()
