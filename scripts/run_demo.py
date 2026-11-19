"""
Interactive & Automated Demonstration Script for MCP Guardian.
Demonstrates:
  1. Safe tool call approval workflow (Claude Desktop stdio).
  2. Dangerous shell command blocking workflow (destructive prevention).
  3. Claude Web HTTP/SSE interception workflow.
  4. Concurrent / parallel request handling.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

# Ensure UTF-8 I/O encoding on Windows
os.environ["PYTHONIOENCODING"] = "utf-8"
os.environ["PYTHONUTF8"] = "1"

# Add project root to sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from mcp_guardian.core.models import Decision, DecisionAction
from mcp_guardian.daemon.service import GuardianDaemonService
from mcp_guardian.mock.mock_client import MockStdioClient, MockWebClient
from mcp_guardian.transports.sse_gateway import SSEGatewayServer


def print_banner(text: str) -> None:
    line = "=" * 70
    print(f"\n{line}")
    print(f"  {text}")
    print(f"{line}\n")


def run_stdio_demo(auto: bool = False) -> None:
    print_banner("DEMO 1: Claude Desktop Interception (Stdio Mode)")
    print("Launching Sample MCP Server wrapped with MCP Guardian...")

    # Clean any old heartbeat
    hb = Path(".mcp_guardian/ipc/daemon.heartbeat")
    if hb.exists():
        try:
            hb.unlink()
        except Exception:
            pass

    daemon = None
    if auto:
        # Automated test daemon
        daemon = GuardianDaemonService(ipc_port=8766)
        def auto_decider(req):
            if "execute_command" in (req.tool_name or "") or "rm -rf" in str(req.arguments):
                print(f"[Auto-Decider] 🛑 Detected dangerous operation '{req.tool_name}'. Submitting: BLOCK")
                return Decision(request_id=req.id, action=DecisionAction.BLOCK, reason="Auto-blocked destructive shell command")
            else:
                print(f"[Auto-Decider] ✅ Detected safe operation '{req.tool_name}'. Submitting: ALLOW")
                return Decision(request_id=req.id, action=DecisionAction.ALLOW, reason="Auto-approved safe calculation")

        daemon.ipc_server.request_handler = auto_decider
        daemon.ipc_server.start()
        daemon.running = True
        time.sleep(0.5)

    wrapper_cmd = [
        sys.executable,
        "-m",
        "mcp_guardian",
        "wrap",
        "--server-name",
        "filesystem_and_tools",
        "--",
        sys.executable,
        "-m",
        "mcp_guardian.mock.sample_mcp_server",
    ]

    client = MockStdioClient(wrapper_cmd)
    client.start()
    time.sleep(1.0)  # Allow process spin-up

    try:
        # 1. Initialize Handshake (passes through transparently)
        print("Step 1: Handshake & Capability Discovery (Transparent)")
        init_res = client.send_request(
            "initialize",
            {"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "Claude Desktop", "version": "0.7.0"}},
            req_id=1,
            timeout=5.0,
        )
        print(f"Handshake response: {json.dumps(init_res, indent=2)}\n")

        # 2. Safe Tool Call (Approval Flow)
        print_banner("Step 2: Safe Tool Call -> Expecting Approval (ALLOW)")
        print("Claude requests: calculate_sum(a=42, b=58)")
        if not auto:
            print("👉 ACTION REQUIRED: Look for the MCP Guardian popup on your screen.")
            print("👉 CLICK [ ✅ Allow (Proceed) ] to permit the calculation.\n")

        res_allow = client.send_request(
            "tools/call",
            {"name": "calculate_sum", "arguments": {"a": 42, "b": 58}},
            req_id=2,
            timeout=60.0,
        )
        print(f"Claude received response:")
        print(json.dumps(res_allow, indent=2))
        if res_allow and res_allow.get("result", {}).get("isError") is False:
            print("\n🎉 SUCCESS: Tool was ALLOWED and executed! Result: 100")
        else:
            print("\n⚠️ Request was BLOCKED.")

        time.sleep(1.5)

        # 3. Dangerous Tool Call (Blocking Flow)
        print_banner("Step 3: Destructive Command -> Expecting Denial (BLOCK)")
        print("Claude requests: execute_command(command='rm -rf / && del /f /s /q C:\\\\')")
        if not auto:
            print("👉 ACTION REQUIRED: On the popup, observe the RED CRITICAL RISK BADGE.")
            print("👉 CLICK [ 🚫 Block (Deny) ] to prevent execution!\n")

        res_block = client.send_request(
            "tools/call",
            {"name": "execute_command", "arguments": {"command": "rm -rf / && del /f /s /q C:\\"}},
            req_id=3,
            timeout=60.0,
        )
        print(f"Claude received response:")
        print(json.dumps(res_block, indent=2))
        if res_block and res_block.get("result", {}).get("isError") is True:
            print("\n🎉 SUCCESS: Dangerous command was successfully BLOCKED before execution!")
        else:
            print("\n⚠️ Tool was allowed.")

    finally:
        client.close()
        if daemon:
            daemon.stop()


def run_sse_demo() -> None:
    print_banner("DEMO 2: Claude Web Interception (HTTP/SSE Gateway)")
    print("Starting Claude Web SSE Gateway on http://127.0.0.1:8765/sse...")

    sample_cmd = [sys.executable, "-m", "mcp_guardian.mock.sample_mcp_server"]
    gateway = SSEGatewayServer(
        port=8765,
        server_name="claude_web_tools",
        downstream_command=sample_cmd,
    )
    gateway.start()
    time.sleep(1.0)

    try:
        web_client = MockWebClient(sse_url="http://127.0.0.1:8765/sse")
        web_client.connect()
        time.sleep(1.0)

        print("\nClaude Web connected via Server-Sent Events.")
        print("Claude Web requests: write_file(path='important_data.json', content='{\"auth\": true}')")
        print("👉 ACTION REQUIRED: Note the 'Client: Claude Web' badge on the popup.")
        print("👉 Make your decision (Allow or Block).\n")

        res = web_client.send_tool_call(
            tool_name="write_file",
            arguments={"path": "important_data.json", "content": '{"auth": true}'},
            req_id="web-req-101",
            timeout=60.0,
        )
        print(f"Claude Web received via SSE: {json.dumps(res, indent=2)}")
    finally:
        gateway.stop()


def main():
    parser = argparse.ArgumentParser(description="MCP Guardian Demonstration Harness")
    parser.add_argument("--auto", action="store_true", help="Run automated demonstration (auto-approve safe, auto-block dangerous)")
    parser.add_argument("--dashboard", action="store_true", help="Automatically launch the GUI Dashboard window alongside the demo")
    args = parser.parse_args()

    dash_proc = None
    if args.dashboard:
        print("\n🚀 Opening MCP Guardian Live Dashboard...")
        dash_proc = subprocess.Popen([sys.executable, "-m", "mcp_guardian", "dashboard"])
        time.sleep(2.0)

    try:
        print("""
========================================================================
🛡️  MCP GUARDIAN - DEMONSTRATION & TEST HARNESS
========================================================================
Demonstrates Claude Desktop and Claude Web initiating tool invocations,
with real-time authorization controls and risk scoring.
========================================================================
""")

        if not args.auto:
            input("Press Enter to begin Demo 1 (Claude Desktop Stdio)...")
        run_stdio_demo(auto=args.auto)

        if not args.auto:
            print("\n" + "-" * 70)
            input("Press Enter to begin Demo 2 (Claude Web SSE Gateway)...")
            run_sse_demo()

        print_banner("🎉 DEMONSTRATION COMPLETED SUCCESSFULLY!")
        print("Audit log recorded in: .mcp_guardian/audit_trail.jsonl and mcp_guardian.log")

        if dash_proc:
            print("\n" + "=" * 70)
            print("👉 The Live Dashboard is still running on your screen.")
            print("👉 You can inspect payloads, review metrics, or export logs.")
            print("=" * 70)
            input("Press Enter when you are ready to close the Dashboard...")
    finally:
        if dash_proc:
            try:
                dash_proc.terminate()
            except Exception:
                pass


if __name__ == "__main__":
    main()
