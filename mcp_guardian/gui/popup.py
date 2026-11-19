"""
Confirmation Popup Modal for MCP Guardian.
Displays an always-on-top, focus-grabbing dialog requiring human authorization before any MCP tool executes.
"""

from __future__ import annotations

import json
import logging
import threading
import time
import tkinter as tk
from tkinter import ttk
from typing import Any, Callable, Optional
from mcp_guardian.core.models import Decision, DecisionAction, MCPRequest, RiskLevel
from mcp_guardian.gui.theme import (
    ACCENT_BLUE,
    ACCENT_GREEN,
    ACCENT_GREEN_HOVER,
    ACCENT_RED,
    ACCENT_RED_HOVER,
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

logger = logging.getLogger("mcp_guardian.popup")


class ConfirmationModal:
    """An individual TopLevel or Root modal dialog presenting an MCP request for user confirmation."""

    def __init__(
        self,
        master: Optional[tk.Tk | tk.Toplevel],
        request: MCPRequest,
        on_decided: Callable[[Decision], None],
        timeout_seconds: float = 60.0,
    ):
        self.request = request
        self.on_decided = on_decided
        self.timeout_seconds = timeout_seconds
        self.start_time = time.time()
        self._decided = False

        self.window = tk.Toplevel(master) if master else tk.Tk()
        self._setup_window()
        self._build_ui()
        self._start_countdown()

    def _setup_window(self) -> None:
        self.window.title("🛡️ MCP Guardian - Request Authorization")
        self.window.geometry("640x540")
        self.window.minsize(560, 480)
        self.window.configure(bg=BG_DARK)

        # Make window stay on top of all active apps and ensure taskbar visibility
        try:
            self.window.deiconify()
            self.window.attributes("-topmost", True)
            self.window.lift()
            self.window.focus_force()
        except Exception:
            pass

        # Handle window close (X button) -> default to BLOCK (fail-safe)
        self.window.protocol("WM_DELETE_WINDOW", self._on_window_close)

        # Audio alert
        try:
            self.window.bell()
        except Exception:
            pass

    def _build_ui(self) -> None:
        # Header banner
        header_frame = tk.Frame(self.window, bg=BG_HEADER, height=60, padx=20, pady=12)
        header_frame.pack(fill=tk.X, side=tk.TOP)

        title_label = tk.Label(
            header_frame,
            text="🛡️ MCP Tool Invocation Request",
            font=FONT_HEADING,
            fg=ACCENT_BLUE,
            bg=BG_HEADER,
        )
        title_label.pack(side=tk.LEFT)

        self.timer_label = tk.Label(
            header_frame,
            text=f"Auto-block in {int(self.timeout_seconds)}s",
            font=FONT_BODY,
            fg=FG_MUTED,
            bg=BG_HEADER,
        )
        self.timer_label.pack(side=tk.RIGHT)

        # Main content card
        content_frame = tk.Frame(self.window, bg=BG_DARK, padx=20, pady=12)
        content_frame.pack(fill=tk.BOTH, expand=True)

        # Metadata grid
        meta_frame = tk.Frame(content_frame, bg=BG_CARD, padx=14, pady=10, relief=tk.FLAT)
        meta_frame.pack(fill=tk.X, pady=(0, 10))

        # Row 1: Client & Server
        r1 = tk.Frame(meta_frame, bg=BG_CARD)
        r1.pack(fill=tk.X, pady=2)
        tk.Label(r1, text="Client:", font=FONT_BODY_BOLD, fg=FG_SECONDARY, bg=BG_CARD, width=10, anchor="w").pack(side=tk.LEFT)
        tk.Label(r1, text=str(self.request.client_type.value), font=FONT_BODY_BOLD, fg=ACCENT_BLUE, bg=BG_CARD).pack(side=tk.LEFT, padx=(0, 20))

        tk.Label(r1, text="Server:", font=FONT_BODY_BOLD, fg=FG_SECONDARY, bg=BG_CARD, width=10, anchor="w").pack(side=tk.LEFT)
        tk.Label(r1, text=self.request.server_name, font=FONT_BODY, fg=FG_PRIMARY, bg=BG_CARD).pack(side=tk.LEFT)

        # Row 2: Tool Name & Risk Badge
        r2 = tk.Frame(meta_frame, bg=BG_CARD)
        r2.pack(fill=tk.X, pady=2)
        tk.Label(r2, text="Tool:", font=FONT_BODY_BOLD, fg=FG_SECONDARY, bg=BG_CARD, width=10, anchor="w").pack(side=tk.LEFT)
        tk.Label(r2, text=self.request.tool_name or self.request.method, font=FONT_BODY_BOLD, fg=FG_PRIMARY, bg=BG_CARD).pack(side=tk.LEFT, padx=(0, 20))

        risk_info = RISK_COLORS.get(self.request.risk_level.value, RISK_COLORS["LOW"])
        risk_badge = tk.Label(
            r2,
            text=f" {risk_info['badge']} ",
            font=FONT_BODY_BOLD,
            fg=risk_info["fg"],
            bg=risk_info["bg"],
            padx=4,
            pady=1,
        )
        risk_badge.pack(side=tk.LEFT)

        # Risk Reasons
        if self.request.risk_reasons:
            r3 = tk.Frame(meta_frame, bg=BG_CARD)
            r3.pack(fill=tk.X, pady=2)
            tk.Label(r3, text="Risk Flag:", font=FONT_BODY_BOLD, fg=FG_SECONDARY, bg=BG_CARD, width=10, anchor="w").pack(side=tk.LEFT)
            tk.Label(r3, text="; ".join(self.request.risk_reasons), font=FONT_BODY, fg=risk_info["bg"], bg=BG_CARD).pack(side=tk.LEFT)

        # Request Parameters Header
        param_label = tk.Label(
            content_frame,
            text="Request Parameters (Payload):",
            font=FONT_SUBHEADING,
            fg=FG_PRIMARY,
            bg=BG_DARK,
            anchor="w",
        )
        param_label.pack(fill=tk.X, pady=(4, 4))

        # Scrollable Text Box for JSON Arguments
        text_frame = tk.Frame(content_frame, bg=BG_CODE)
        text_frame.pack(fill=tk.BOTH, expand=True)

        scrollbar = tk.Scrollbar(text_frame)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        self.text_box = tk.Text(
            text_frame,
            font=FONT_CODE,
            fg=FG_PRIMARY,
            bg=BG_CODE,
            insertbackground=FG_PRIMARY,
            selectbackground=ACCENT_BLUE,
            selectforeground=BG_DARK,
            yscrollcommand=scrollbar.set,
            padx=10,
            pady=8,
            relief=tk.FLAT,
            wrap=tk.WORD,
        )
        self.text_box.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.config(command=self.text_box.yview)

        # Format parameters JSON
        try:
            formatted_json = json.dumps(self.request.arguments, indent=2, ensure_ascii=False)
        except Exception:
            formatted_json = str(self.request.arguments)

        self.text_box.insert(tk.END, formatted_json)
        self.text_box.config(state=tk.DISABLED)

        # Bottom Bar: Remember Checkbox & Action Buttons
        bottom_frame = tk.Frame(self.window, bg=BG_DARK, padx=20, pady=14)
        bottom_frame.pack(fill=tk.X, side=tk.BOTTOM)

        self.remember_var = tk.BooleanVar(value=False)
        self.remember_check = tk.Checkbutton(
            bottom_frame,
            text="Remember decision for this tool during this session",
            variable=self.remember_var,
            font=FONT_BODY,
            fg=FG_SECONDARY,
            bg=BG_DARK,
            selectcolor=BG_CARD,
            activebackground=BG_DARK,
            activeforeground=FG_PRIMARY,
        )
        self.remember_check.pack(side=tk.LEFT)

        btn_box = tk.Frame(bottom_frame, bg=BG_DARK)
        btn_box.pack(side=tk.RIGHT)

        # BLOCK Button
        self.block_btn = tk.Button(
            btn_box,
            text="🚫 Block (Deny)",
            font=FONT_BUTTON,
            fg="#11111b",
            bg=ACCENT_RED,
            activebackground=ACCENT_RED_HOVER,
            activeforeground="#11111b",
            padx=16,
            pady=6,
            relief=tk.FLAT,
            cursor="hand2",
            command=self._on_block,
        )
        self.block_btn.pack(side=tk.LEFT, padx=6)

        # ALLOW Button
        self.allow_btn = tk.Button(
            btn_box,
            text="✅ Allow (Proceed)",
            font=FONT_BUTTON,
            fg="#11111b",
            bg=ACCENT_GREEN,
            activebackground=ACCENT_GREEN_HOVER,
            activeforeground="#11111b",
            padx=16,
            pady=6,
            relief=tk.FLAT,
            cursor="hand2",
            command=self._on_allow,
        )
        self.allow_btn.pack(side=tk.LEFT, padx=6)

    def _start_countdown(self) -> None:
        """Updates the auto-block countdown timer each second."""
        if self._decided:
            return
        elapsed = time.time() - self.start_time
        remaining = max(0, int(self.timeout_seconds - elapsed))
        if remaining > 0:
            self.timer_label.config(text=f"Auto-block in {remaining}s")
            self.window.after(1000, self._start_countdown)
        else:
            self.timer_label.config(text="Timed out!")
            self._on_timeout()

    def _resolve(self, action: DecisionAction, reason: str) -> None:
        if self._decided:
            return
        self._decided = True
        decision = Decision(
            request_id=self.request.id,
            action=action,
            reason=reason,
            remember_session=self.remember_var.get(),
            timestamp=time.time(),
        )
        try:
            self.on_decided(decision)
        except Exception as e:
            logger.error(f"Error in on_decided callback: {e}")

        try:
            self.window.destroy()
        except Exception:
            pass

    def _on_allow(self) -> None:
        self._resolve(DecisionAction.ALLOW, "Approved by user via confirmation popup")

    def _on_block(self) -> None:
        self._resolve(DecisionAction.BLOCK, "Blocked by user via confirmation popup")

    def _on_window_close(self) -> None:
        self._resolve(DecisionAction.BLOCK, "User dismissed authorization popup (Default fail-safe: Block)")

    def _on_timeout(self) -> None:
        self._resolve(DecisionAction.BLOCK, f"Request timed out after {self.timeout_seconds}s (Default: Block)")


def ask_user_confirmation_sync(
    request: MCPRequest,
    timeout_seconds: float = 60.0,
) -> Decision:
    """
    Synchronously displays the confirmation modal in a dedicated thread/window.
    Blocks the calling thread until a decision is made or timeout occurs.
    """
    decision_holder: list[Optional[Decision]] = [None]
    done_event = threading.Event()

    def run_gui():
        try:
            # Create root tk.Tk() directly so it has full taskbar presence and visibility
            modal = ConfirmationModal(
                master=None,
                request=request,
                on_decided=lambda dec: decision_holder.__setitem__(0, dec),
                timeout_seconds=timeout_seconds,
            )
            modal.window.mainloop()
        except Exception as e:
            logger.error(f"Error in confirmation GUI: {e}")
        finally:
            done_event.set()

    gui_thread = threading.Thread(target=run_gui, daemon=True)
    gui_thread.start()

    done_event.wait(timeout=timeout_seconds + 3.0)

    if decision_holder[0] is not None:
        return decision_holder[0]

    return Decision(
        request_id=request.id,
        action=DecisionAction.BLOCK,
        reason=f"Confirmation timed out after {timeout_seconds}s or window dismissed",
        remember_session=False,
        timestamp=time.time(),
    )
