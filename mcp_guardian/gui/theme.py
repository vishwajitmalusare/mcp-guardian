"""
Modern Dark Theme Styling for MCP Guardian GUI.
Provides colors, typography, and styling configurations.
"""

# Palette inspired by modern developer tooling
BG_DARK = "#181825"
BG_CARD = "#1e1e2e"
BG_HEADER = "#11111b"
BG_CODE = "#11111b"

FG_PRIMARY = "#cdd6f4"
FG_SECONDARY = "#a6adc8"
FG_MUTED = "#6c7086"

ACCENT_BLUE = "#89b4fa"
ACCENT_GREEN = "#a6e3a1"
ACCENT_GREEN_HOVER = "#94e2d5"
ACCENT_RED = "#f38ba8"
ACCENT_RED_HOVER = "#eba0ac"
ACCENT_YELLOW = "#f9e2af"
ACCENT_ORANGE = "#fab387"

# Risk Badges styling
RISK_COLORS = {
    "CRITICAL": {"bg": "#f38ba8", "fg": "#11111b", "badge": "CRITICAL RISK 🚨"},
    "HIGH": {"bg": "#fab387", "fg": "#11111b", "badge": "HIGH RISK ⚠️"},
    "MEDIUM": {"bg": "#f9e2af", "fg": "#11111b", "badge": "MEDIUM RISK ⚡"},
    "LOW": {"bg": "#89b4fa", "fg": "#11111b", "badge": "LOW RISK ℹ️"},
    "SAFE": {"bg": "#a6e3a1", "fg": "#11111b", "badge": "SAFE OPERATION 🛡️"},
}

FONT_HEADING = ("Segoe UI", 13, "bold")
FONT_SUBHEADING = ("Segoe UI", 11, "bold")
FONT_BODY = ("Segoe UI", 10)
FONT_BODY_BOLD = ("Segoe UI", 10, "bold")
FONT_CODE = ("Consolas", 10)
FONT_BUTTON = ("Segoe UI", 11, "bold")
