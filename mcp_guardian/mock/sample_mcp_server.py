"""
Sample MCP Stdio Server.
Provides realistic MCP tools (safe computation, file operations, shell execution)
for demonstration and test suites.
"""

from __future__ import annotations

import json
import os
import platform
import subprocess
import sys
from typing import Any, Dict


SERVER_NAME = "demo-mcp-server"
SERVER_VERSION = "1.0.0"

for stream_name in ("stdin", "stdout", "stderr"):
    stream = getattr(sys, stream_name, None)
    if stream and hasattr(stream, "reconfigure"):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

TOOLS_METADATA = [
    {
        "name": "calculate_sum",
        "description": "Calculates the sum of two numbers.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "a": {"type": "number", "description": "First number"},
                "b": {"type": "number", "description": "Second number"},
            },
            "required": ["a", "b"],
        },
    },
    {
        "name": "read_system_info",
        "description": "Reads basic host operating system and platform information.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "category": {"type": "string", "enum": ["os", "python", "environment"]},
            },
        },
    },
    {
        "name": "write_file",
        "description": "Writes text content to a specified file path.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Destination file path"},
                "content": {"type": "string", "description": "Text content to write"},
            },
            "required": ["path", "content"],
        },
    },
    {
        "name": "execute_command",
        "description": "Executes an arbitrary shell or command prompt command.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "Shell command to run"},
            },
            "required": ["command"],
        },
    },
]


def send_response(response: Dict[str, Any]) -> None:
    line = json.dumps(response, ensure_ascii=False) + "\n"
    if hasattr(sys.stdout, "buffer"):
        sys.stdout.buffer.write(line.encode("utf-8", errors="replace"))
        sys.stdout.buffer.flush()
    else:
        sys.stdout.write(line)
        sys.stdout.flush()


def handle_tools_call(tool_name: str, arguments: Dict[str, Any]) -> str:
    """Executes the tool logic and returns the output string."""
    if tool_name == "calculate_sum":
        a = arguments.get("a", 0)
        b = arguments.get("b", 0)
        return f"Result: {a} + {b} = {a + b}"

    elif tool_name == "read_system_info":
        cat = arguments.get("category", "os")
        if cat == "python":
            return f"Python {platform.python_version()} on {platform.system()}"
        elif cat == "environment":
            return f"User: {os.environ.get('USERNAME') or os.environ.get('USER')}, CWD: {os.getcwd()}"
        return f"System: {platform.system()} {platform.release()} ({platform.machine()})"

    elif tool_name == "write_file":
        path = arguments.get("path", "test_output.txt")
        content = arguments.get("content", "")
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        return f"File successfully written to: {path} ({len(content)} bytes)"

    elif tool_name == "execute_command":
        cmd = arguments.get("command", "echo Hello")
        try:
            res = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=10)
            output = res.stdout or res.stderr or "(No output)"
            return f"Command executed (exit code {res.returncode}):\n{output}"
        except Exception as e:
            return f"Command execution failed: {e}"

    return f"Error: Unknown tool '{tool_name}'"


def main():
    sys.stderr.write(f"[{SERVER_NAME}] Server started. Listening on stdin...\n")
    sys.stderr.flush()

    for line in sys.stdin:
        if not line:
            break
        cleaned = line.strip()
        if not cleaned:
            continue

        try:
            message = json.loads(cleaned)
        except json.JSONDecodeError:
            continue

        req_id = message.get("id")
        method = message.get("method")
        params = message.get("params", {}) or {}

        # 1. Initialize
        if method == "initialize":
            send_response({
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
                },
            })

        # 2. Initialized notification
        elif method == "notifications/initialized":
            pass

        # 3. Tools list
        elif method == "tools/list":
            send_response({
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {"tools": TOOLS_METADATA},
            })

        # 4. Tools call
        elif method == "tools/call":
            tool_name = params.get("name", "")
            arguments = params.get("arguments", {})
            sys.stderr.write(f"[{SERVER_NAME}] EXECUTING TOOL: {tool_name} with args {arguments}\n")
            sys.stderr.flush()

            output_text = handle_tools_call(tool_name, arguments)

            send_response({
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "content": [{"type": "text", "text": output_text}],
                    "isError": False,
                },
            })

        # 5. Ping
        elif method == "ping":
            send_response({"jsonrpc": "2.0", "id": req_id, "result": {}})

        else:
            if req_id is not None:
                send_response({
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "error": {"code": -32601, "message": f"Method not found: {method}"},
                })


if __name__ == "__main__":
    main()
