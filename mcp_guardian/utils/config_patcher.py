"""
Claude Desktop Configuration Inspector & Automatic Patcher.
Automatically discovers, inspects, backs up, and configures Claude Desktop to route through MCP Guardian.
"""

from __future__ import annotations

import json
import os
import platform
import shutil
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


def get_claude_config_path() -> Path:
    """Returns the expected path to claude_desktop_config.json based on operating system."""
    system = platform.system()
    if system == "Windows":
        app_data = os.environ.get("APPDATA", "")
        if app_data:
            return Path(app_data) / "Claude" / "claude_desktop_config.json"
        return Path.home() / "AppData" / "Roaming" / "Claude" / "claude_desktop_config.json"
    elif system == "Darwin":  # macOS
        return Path.home() / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"
    else:  # Linux / Unix
        config_home = os.environ.get("XDG_CONFIG_HOME", "")
        if config_home:
            return Path(config_home) / "Claude" / "claude_desktop_config.json"
        return Path.home() / ".config" / "Claude" / "claude_desktop_config.json"


class ClaudeConfigManager:
    """Manages reading, patching, and restoring Claude Desktop configuration."""

    def __init__(self, config_path: Optional[Path] = None):
        self.config_path = config_path or get_claude_config_path()

    def exists(self) -> bool:
        return self.config_path.exists()

    def read_config(self) -> Dict[str, Any]:
        """Reads and parses the configuration file, returning an empty dict if not found."""
        if not self.config_path.exists():
            return {}
        try:
            with open(self.config_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}

    def write_config(self, config_data: Dict[str, Any]) -> None:
        """Writes configuration data cleanly with indentation."""
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.config_path, "w", encoding="utf-8") as f:
            json.dump(config_data, f, indent=2, ensure_ascii=False)

    def is_server_wrapped(self, server_cfg: Dict[str, Any]) -> bool:
        """Checks if a server configuration entry is already wrapped with mcp_guardian."""
        args = server_cfg.get("args", [])
        args_str = " ".join(args)
        cmd = server_cfg.get("command", "")
        return "mcp_guardian" in cmd or "mcp_guardian" in args_str

    def backup_config(self) -> Optional[Path]:
        """Creates a timestamped backup of the current config."""
        if not self.config_path.exists():
            return None
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        backup_path = self.config_path.parent / f"claude_desktop_config.backup_{timestamp}.json"
        shutil.copy2(self.config_path, backup_path)
        return backup_path

    def wrap_all_servers(self) -> Tuple[int, Optional[Path]]:
        """
        Wraps all configured mcpServers with MCP Guardian stdio wrapper.
        Returns (number of patched servers, backup_path).
        """
        config = self.read_config()
        if "mcpServers" not in config or not config["mcpServers"]:
            return 0, None

        backup_path = self.backup_config()
        patched_count = 0
        py_exe = sys.executable

        for srv_name, srv_data in config["mcpServers"].items():
            if self.is_server_wrapped(srv_data):
                continue

            orig_cmd = srv_data.get("command", "")
            orig_args = srv_data.get("args", [])

            # Formulate wrapper command
            wrapper_args = [
                "-m",
                "mcp_guardian",
                "wrap",
                "--server-name",
                srv_name,
                "--",
                orig_cmd,
            ] + orig_args

            srv_data["command"] = py_exe
            srv_data["args"] = wrapper_args
            # Tag metadata to preserve original info
            srv_data["_mcp_guardian_original"] = {
                "command": orig_cmd,
                "args": orig_args,
            }
            patched_count += 1

        self.write_config(config)
        return patched_count, backup_path

    def unwrap_all_servers(self) -> int:
        """
        Restores original server configurations.
        Returns the number of restored servers.
        """
        config = self.read_config()
        if "mcpServers" not in config:
            return 0

        restored_count = 0
        for srv_name, srv_data in config["mcpServers"].items():
            if not self.is_server_wrapped(srv_data):
                continue

            orig = srv_data.get("_mcp_guardian_original")
            if orig:
                srv_data["command"] = orig["command"]
                srv_data["args"] = orig["args"]
                srv_data.pop("_mcp_guardian_original", None)
                restored_count += 1
            else:
                # Fallback parse from args
                args = srv_data.get("args", [])
                if "--" in args:
                    idx = args.index("--")
                    sub_args = args[idx + 1 :]
                    if sub_args:
                        srv_data["command"] = sub_args[0]
                        srv_data["args"] = sub_args[1:]
                        restored_count += 1

        self.write_config(config)
        return restored_count
