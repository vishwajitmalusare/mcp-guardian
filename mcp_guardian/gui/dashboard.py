"""
Live Monitoring Dashboard & Control Center for MCP Guardian.
Provides real-time visibility into intercepted requests, audit trails, active sessions, and metrics.
"""

from __future__ import annotations

import csv
import json
import logging
import os
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from typing import Any, Dict, List, Optional
from mcp_guardian.core.audit import AuditLogger, default_audit_logger
from mcp_guardian.core.manager import ConcurrencyManager
from mcp_guardian.core.models import AuditEntry, Decision, DecisionAction, MCPRequest
from mcp_guardian.gui.popup import ConfirmationModal
from mcp_guardian.gui.theme import (
    ACCENT_BLUE,
    ACCENT_GREEN,
    ACCENT_ORANGE,
    ACCENT_RED,
    ACCENT_YELLOW,
    BG_CARD,
    BG_CODE,
    BG_DARK,
    BG_HEADER,
    FG_MUTED,
    FG_PRIMARY,
    FG_SECONDARY,
    FONT_BODY,
    FONT_BODY_BOLD,
    FONT_BUTTON,
    FONT_CODE,
    FONT_HEADING,
    FONT_SUBHEADING,
    RISK_COLORS,
)

logger = logging.getLogger("mcp_guardian.dashboard")


class GuardianDashboard:
    """Central interactive GUI dashboard for monitoring and managing MCP authorization."""

    def __init__(
        self,
        manager: ConcurrencyManager,
        audit_logger: Optional[AuditLogger] = None,
        on_start_sse: Optional[Any] = None,
        on_stop_sse: Optional[Any] = None,
    ):
        self.manager = manager
        self.audit = audit_logger or default_audit_logger
        self.on_start_sse = on_start_sse
        self.on_stop_sse = on_stop_sse
        self.sse_running = False

        self.root = tk.Tk()
        self._setup_window()
        self._build_ui()
        self._subscribe_events()

    def _setup_window(self) -> None:
        self.root.title("🛡️ MCP Guardian - Security & Authorization Console")
        self.root.geometry("1100x720")
        self.root.minsize(900, 600)
        self.root.configure(bg=BG_DARK)

    def _build_ui(self) -> None:
        # 1. Top Navigation Bar
        top_bar = tk.Frame(self.root, bg=BG_HEADER, height=64, padx=20, pady=12)
        top_bar.pack(fill=tk.X, side=tk.TOP)

        title_box = tk.Frame(top_bar, bg=BG_HEADER)
        title_box.pack(side=tk.LEFT)

        tk.Label(
            title_box,
            text="🛡️ MCP GUARDIAN",
            font=FONT_HEADING,
            fg=ACCENT_BLUE,
            bg=BG_HEADER,
        ).pack(side=tk.LEFT)

        tk.Label(
            title_box,
            text=" | Real-Time MCP Traffic Interceptor & Firewall",
            font=FONT_SUBHEADING,
            fg=FG_SECONDARY,
            bg=BG_HEADER,
        ).pack(side=tk.LEFT, padx=6)

        # Status pills on top right
        status_box = tk.Frame(top_bar, bg=BG_HEADER)
        status_box.pack(side=tk.RIGHT)

        self.status_pill = tk.Label(
            status_box,
            text=" ● MONITORING ACTIVE ",
            font=FONT_BODY_BOLD,
            fg="#11111b",
            bg=ACCENT_GREEN,
            padx=8,
            pady=3,
        )
        self.status_pill.pack(side=tk.LEFT, padx=4)

        # 2. Metrics Bar
        metrics_frame = tk.Frame(self.root, bg=BG_CARD, padx=20, pady=10)
        metrics_frame.pack(fill=tk.X, padx=16, pady=(12, 6))

        self.lbl_total = self._create_metric_widget(metrics_frame, "Total Requests", "0", ACCENT_BLUE)
        self.lbl_allowed = self._create_metric_widget(metrics_frame, "Allowed", "0", ACCENT_GREEN)
        self.lbl_blocked = self._create_metric_widget(metrics_frame, "Blocked", "0", ACCENT_RED)
        self.lbl_rate = self._create_metric_widget(metrics_frame, "Block Rate", "0%", ACCENT_YELLOW)

        # Quick Control buttons on the right of metrics
        ctrl_frame = tk.Frame(metrics_frame, bg=BG_CARD)
        ctrl_frame.pack(side=tk.RIGHT, padx=10)

        self.btn_export = tk.Button(
            ctrl_frame,
            text="📥 Export Audit Log",
            font=FONT_BODY,
            fg=FG_PRIMARY,
            bg=BG_DARK,
            relief=tk.FLAT,
            padx=10,
            pady=4,
            command=self._export_audit_log,
        )
        self.btn_export.pack(side=tk.LEFT, padx=4)

        self.btn_clear_rules = tk.Button(
            ctrl_frame,
            text="🔄 Reset Rules",
            font=FONT_BODY,
            fg=FG_PRIMARY,
            bg=BG_DARK,
            relief=tk.FLAT,
            padx=10,
            pady=4,
            command=self._clear_rules,
        )
        self.btn_clear_rules.pack(side=tk.LEFT, padx=4)

        # 3. Middle Area: Notebook / Split Pane (Live Traffic & Pending Queue)
        paned = tk.PanedWindow(self.root, orient=tk.VERTICAL, bg=BG_DARK, sashwidth=6, sashrelief=tk.FLAT)
        paned.pack(fill=tk.BOTH, expand=True, padx=16, pady=6)

        # Pending Requests Panel
        pending_container = tk.Frame(paned, bg=BG_DARK)
        paned.add(pending_container, height=180)

        pending_hdr = tk.Frame(pending_container, bg=BG_DARK)
        pending_hdr.pack(fill=tk.X, pady=(0, 4))
        tk.Label(
            pending_hdr,
            text="⏳ PENDING AUTHORIZATION QUEUE",
            font=FONT_SUBHEADING,
            fg=ACCENT_ORANGE,
            bg=BG_DARK,
        ).pack(side=tk.LEFT)

        self.pending_count_label = tk.Label(
            pending_hdr,
            text="(0 pending requests)",
            font=FONT_BODY,
            fg=FG_MUTED,
            bg=BG_DARK,
        )
        self.pending_count_label.pack(side=tk.LEFT, padx=8)

        # Treeview for Pending Requests
        pending_tree_frame = tk.Frame(pending_container, bg=BG_CARD)
        pending_tree_frame.pack(fill=tk.BOTH, expand=True)

        self.pending_tree = ttk.Treeview(
            pending_tree_frame,
            columns=("id", "client", "server", "tool", "risk", "arguments"),
            show="headings",
            selectmode="browse",
        )
        self.pending_tree.heading("id", text="ID")
        self.pending_tree.heading("client", text="Client Source")
        self.pending_tree.heading("server", text="Server")
        self.pending_tree.heading("tool", text="Tool Name")
        self.pending_tree.heading("risk", text="Risk Assessment")
        self.pending_tree.heading("arguments", text="Arguments Preview")

        self.pending_tree.column("id", width=60, anchor="center")
        self.pending_tree.column("client", width=120)
        self.pending_tree.column("server", width=110)
        self.pending_tree.column("tool", width=140)
        self.pending_tree.column("risk", width=130, anchor="center")
        self.pending_tree.column("arguments", width=400)

        pending_scroll = tk.Scrollbar(pending_tree_frame, orient=tk.VERTICAL, command=self.pending_tree.yview)
        self.pending_tree.configure(yscrollcommand=pending_scroll.set)
        self.pending_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        pending_scroll.pack(side=tk.RIGHT, fill=tk.Y)

        # Action buttons for selected pending request
        pending_act_bar = tk.Frame(pending_container, bg=BG_DARK, pady=4)
        pending_act_bar.pack(fill=tk.X)

        tk.Button(
            pending_act_bar,
            text="🚫 Block Selected",
            font=FONT_BODY_BOLD,
            fg="#11111b",
            bg=ACCENT_RED,
            padx=12,
            pady=3,
            relief=tk.FLAT,
            command=self._block_selected_pending,
        ).pack(side=tk.RIGHT, padx=4)

        tk.Button(
            pending_act_bar,
            text="✅ Allow Selected",
            font=FONT_BODY_BOLD,
            fg="#11111b",
            bg=ACCENT_GREEN,
            padx=12,
            pady=3,
            relief=tk.FLAT,
            command=self._allow_selected_pending,
        ).pack(side=tk.RIGHT, padx=4)

        # Audit Trail Panel
        audit_container = tk.Frame(paned, bg=BG_DARK)
        paned.add(audit_container, height=280)

        audit_hdr = tk.Frame(audit_container, bg=BG_DARK)
        audit_hdr.pack(fill=tk.X, pady=(4, 4))
        tk.Label(
            audit_hdr,
            text="📜 REAL-TIME AUDIT TRAIL & TRAFFIC LOG",
            font=FONT_SUBHEADING,
            fg=ACCENT_BLUE,
            bg=BG_DARK,
        ).pack(side=tk.LEFT)

        audit_tree_frame = tk.Frame(audit_container, bg=BG_CARD)
        audit_tree_frame.pack(fill=tk.BOTH, expand=True)

        self.audit_tree = ttk.Treeview(
            audit_tree_frame,
            columns=("time", "client", "server", "tool", "decision", "latency", "details"),
            show="headings",
            selectmode="browse",
        )
        self.audit_tree.heading("time", text="Time")
        self.audit_tree.heading("client", text="Client")
        self.audit_tree.heading("server", text="Server")
        self.audit_tree.heading("tool", text="Tool")
        self.audit_tree.heading("decision", text="Decision")
        self.audit_tree.heading("latency", text="Latency")
        self.audit_tree.heading("details", text="Reason / Details")

        self.audit_tree.column("time", width=90, anchor="center")
        self.audit_tree.column("client", width=120)
        self.audit_tree.column("server", width=110)
        self.audit_tree.column("tool", width=140)
        self.audit_tree.column("decision", width=110, anchor="center")
        self.audit_tree.column("latency", width=80, anchor="center")
        self.audit_tree.column("details", width=350)

        audit_scroll = tk.Scrollbar(audit_tree_frame, orient=tk.VERTICAL, command=self.audit_tree.yview)
        self.audit_tree.configure(yscrollcommand=audit_scroll.set)
        self.audit_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        audit_scroll.pack(side=tk.RIGHT, fill=tk.Y)

        self.audit_tree.bind("<<TreeviewSelect>>", self._on_audit_select)

        # 4. Bottom Inspection Pane
        inspect_frame = tk.Frame(self.root, bg=BG_HEADER, height=120, padx=16, pady=8)
        inspect_frame.pack(fill=tk.X, side=tk.BOTTOM)

        tk.Label(
            inspect_frame,
            text="Payload Inspector:",
            font=FONT_BODY_BOLD,
            fg=FG_SECONDARY,
            bg=BG_HEADER,
        ).pack(side=tk.TOP, anchor="w")

        self.inspect_text = tk.Text(
            inspect_frame,
            height=4,
            font=FONT_CODE,
            fg=FG_PRIMARY,
            bg=BG_CODE,
            relief=tk.FLAT,
            wrap=tk.WORD,
        )
        self.inspect_text.pack(fill=tk.X, pady=(4, 0))

        # Apply ttk styling for dark theme tables
        self._apply_treeview_style()

    def _create_metric_widget(self, parent: tk.Frame, title: str, val: str, color: str) -> tk.Label:
        box = tk.Frame(parent, bg=BG_CARD, padx=16, pady=4)
        box.pack(side=tk.LEFT)
        tk.Label(box, text=title.upper(), font=("Segoe UI", 8, "bold"), fg=FG_MUTED, bg=BG_CARD).pack(anchor="w")
        lbl = tk.Label(box, text=val, font=("Segoe UI", 16, "bold"), fg=color, bg=BG_CARD)
        lbl.pack(anchor="w")
        return lbl

    def _apply_treeview_style(self) -> None:
        style = ttk.Style()
        style.theme_use("clam")
        style.configure(
            "Treeview",
            background=BG_CARD,
            foreground=FG_PRIMARY,
            fieldbackground=BG_CARD,
            font=FONT_BODY,
            rowheight=26,
            borderwidth=0,
        )
        style.configure(
            "Treeview.Heading",
            background=BG_HEADER,
            foreground=ACCENT_BLUE,
            font=FONT_BODY_BOLD,
            borderwidth=0,
        )
        style.map("Treeview", background=[("selected", "#313244")], foreground=[("selected", FG_PRIMARY)])

    def _subscribe_events(self) -> None:
        """Connects the manager and audit logger to this dashboard."""
        self.manager.set_request_handler(self._on_incoming_request)
        self.audit.subscribe(self._on_new_audit_entry)

    def _on_incoming_request(self, request: MCPRequest) -> None:
        """Invoked when a new request arrives that requires authorization."""
        # Schedule GUI updates on Tk mainloop thread
        self.root.after(0, self._render_incoming_request, request)

    def _render_incoming_request(self, request: MCPRequest) -> None:
        # Add to pending treeview safely
        args_str = json.dumps(request.arguments, ensure_ascii=False)
        if len(args_str) > 100:
            args_str = args_str[:97] + "..."

        req_id_str = str(request.id)
        if self.pending_tree.exists(req_id_str):
            try:
                self.pending_tree.delete(req_id_str)
            except Exception:
                pass

        self.pending_tree.insert(
            "",
            tk.END,
            iid=req_id_str,
            values=(
                req_id_str,
                request.client_type.value,
                request.server_name,
                request.tool_name or request.method,
                request.risk_level.value,
                args_str,
            ),
        )
        self._update_pending_count()

        # Display popup modal directly on top of all windows
        def on_modal_decided(decision: Decision):
            self.manager.submit_decision(request.id, decision)
            self._remove_pending(request.id)

        ConfirmationModal(
            master=self.root,
            request=request,
            on_decided=on_modal_decided,
            timeout_seconds=self.manager.policy.default_timeout_seconds,
        )

    def _remove_pending(self, req_id: Any) -> None:
        try:
            if self.pending_tree.exists(str(req_id)):
                self.pending_tree.delete(str(req_id))
            self._update_pending_count()
        except Exception:
            pass

    def _update_pending_count(self) -> None:
        count = len(self.pending_tree.get_children())
        self.pending_count_label.config(text=f"({count} pending requests)")

    def _allow_selected_pending(self) -> None:
        selected = self.pending_tree.selection()
        if not selected:
            return
        req_id = selected[0]
        decision = Decision(request_id=req_id, action=DecisionAction.ALLOW, reason="Approved via Dashboard Table")
        self.manager.submit_decision(req_id, decision)
        self._remove_pending(req_id)

    def _block_selected_pending(self) -> None:
        selected = self.pending_tree.selection()
        if not selected:
            return
        req_id = selected[0]
        decision = Decision(request_id=req_id, action=DecisionAction.BLOCK, reason="Blocked via Dashboard Table")
        self.manager.submit_decision(req_id, decision)
        self._remove_pending(req_id)

    def _on_new_audit_entry(self, entry: AuditEntry) -> None:
        """Called by audit logger on background thread; dispatch to Tk mainloop."""
        self.root.after(0, self._render_audit_entry, entry)

    def _render_audit_entry(self, entry: AuditEntry) -> None:
        time_str = time.strftime("%H:%M:%S", time.localtime(entry.request.timestamp))
        status_text = "ALLOWED ✅" if entry.decision.is_allowed else "BLOCKED 🚫"

        self.audit_tree.insert(
            "",
            0,  # Insert at top
            values=(
                time_str,
                entry.request.client_type.value,
                entry.request.server_name,
                entry.request.tool_name or entry.request.method,
                status_text,
                f"{entry.duration_ms:.1f}ms",
                entry.decision.reason,
            ),
        )
        self._update_metrics()

    def _update_metrics(self) -> None:
        stats = self.audit.get_stats()
        self.lbl_total.config(text=str(stats["total"]))
        self.lbl_allowed.config(text=str(stats["allowed"]))
        self.lbl_blocked.config(text=str(stats["blocked"]))
        self.lbl_rate.config(text=f"{stats['block_rate_percent']}%")

    def _on_audit_select(self, event: Any) -> None:
        selected = self.audit_tree.selection()
        if not selected:
            return
        item_vals = self.audit_tree.item(selected[0], "values")
        if item_vals:
            recent_entries = self.audit.get_recent(limit=100)
            for e in reversed(recent_entries):
                if e.request.server_name == item_vals[2] and (e.request.tool_name or e.request.method) == item_vals[3]:
                    self.inspect_text.delete("1.0", tk.END)
                    payload_info = {
                        "id": e.request.id,
                        "client": e.request.client_type.value,
                        "server": e.request.server_name,
                        "tool": e.request.tool_name,
                        "arguments": e.request.arguments,
                        "decision": e.decision.to_dict(),
                        "duration_ms": e.duration_ms,
                    }
                    self.inspect_text.insert(tk.END, json.dumps(payload_info, indent=2, ensure_ascii=False))
                    break

    def _clear_rules(self) -> None:
        self.manager.policy.clear_session_rules()
        messagebox.showinfo("Session Rules Reset", "All remembered session authorization rules have been cleared.")

    def _export_audit_log(self) -> None:
        file_path = filedialog.asksaveasfilename(
            defaultextension=".csv",
            filetypes=[("CSV Files", "*.csv"), ("JSON Files", "*.json")],
            title="Export MCP Audit Trail",
        )
        if not file_path:
            return

        entries = self.audit.get_recent(limit=1000)
        try:
            if file_path.endswith(".json"):
                with open(file_path, "w", encoding="utf-8") as f:
                    json.dump([e.to_dict() for e in entries], f, indent=2, ensure_ascii=False)
            else:
                with open(file_path, "w", newline="", encoding="utf-8") as f:
                    writer = csv.writer(f)
                    writer.writerow(["Timestamp", "Client", "Server", "Tool", "Decision", "Latency_MS", "Reason", "Arguments"])
                    for e in entries:
                        writer.writerow([
                            time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(e.request.timestamp)),
                            e.request.client_type.value,
                            e.request.server_name,
                            e.request.tool_name or e.request.method,
                            e.decision.action.value,
                            f"{e.duration_ms:.1f}",
                            e.decision.reason,
                            json.dumps(e.request.arguments, ensure_ascii=False),
                        ])
            messagebox.showinfo("Export Successful", f"Exported {len(entries)} audit entries to:\n{file_path}")
        except Exception as e:
            messagebox.showerror("Export Failed", f"Failed to export log: {e}")

    def run(self) -> None:
        """Starts the Tkinter GUI mainloop."""
        self.root.mainloop()
