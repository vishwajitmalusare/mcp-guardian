"""
Command Line Interface (CLI) for MCP Guardian.
Provides subcommands: wrap, dashboard, gateway, configure, demo, and sample-server.
"""

from __future__ import annotations

import argparse
import sys
import threading
import time
from typing import List
from mcp_guardian.core.audit import default_audit_logger
from mcp_guardian.daemon.service import GuardianDaemonService
from mcp_guardian.transports.stdio_wrapper import StdioWrapper
from mcp_guardian.transports.sse_gateway import SSEGatewayServer
from mcp_guardian.utils.config_patcher import ClaudeConfigManager


def cmd_wrap(args: argparse.Namespace) -> int:
    """Wraps an MCP server stdio process to intercept Claude Desktop requests."""
    if not args.cmd:
        sys.stderr.write("Error: No command specified to wrap. Usage: mcp-guardian wrap -- <command> [args...]\n")
        return 1

    wrapper = StdioWrapper(
        command_args=args.cmd,
        server_name=args.server_name,
        ipc_port=args.ipc_port,
    )
    return wrapper.start()


def cmd_dashboard(args: argparse.Namespace) -> int:
    """Launches the interactive GUI dashboard and background IPC daemon."""
    from mcp_guardian.gui.dashboard import GuardianDashboard

    # 1. Start daemon service
    daemon = GuardianDaemonService(ipc_port=args.ipc_port)
    daemon.start(interactive_gui=False)

    # 2. Optionally start SSE gateway
    sse_server = None
    if args.enable_sse:
        sample_server_cmd = [sys.executable, "-m", "mcp_guardian.mock.sample_mcp_server"]
        sse_server = SSEGatewayServer(
            port=args.sse_port,
            downstream_command=sample_server_cmd,
        )
        sse_server.start()

    # 3. Launch Tkinter Dashboard
    dashboard = GuardianDashboard(
        manager=daemon.manager,
        audit_logger=default_audit_logger,
    )

    try:
        dashboard.run()
    finally:
        daemon.stop()
        if sse_server:
            sse_server.stop()
    return 0


def cmd_gateway(args: argparse.Namespace) -> int:
    """Runs the HTTP/SSE Reverse Proxy Gateway for Claude Web."""
    downstream_cmd = None
    if args.upstream:
        downstream_cmd = args.upstream.split()
    else:
        # Default to built-in sample server
        downstream_cmd = [sys.executable, "-m", "mcp_guardian.mock.sample_mcp_server"]

    gateway = SSEGatewayServer(
        host=args.host,
        port=args.port,
        server_name=args.name,
        downstream_command=downstream_cmd,
    )
    gateway.start()
    print(f"\n=======================================================")
    print(f"🛡️  MCP Guardian SSE Gateway Active for Claude Web")
    print(f"=======================================================")
    print(f" • SSE Endpoint URL:  http://{args.host}:{args.port}/sse")
    print(f" • Downstream Server: {' '.join(downstream_cmd)}")
    print(f"\nPress Ctrl+C to terminate gateway.\n")

    try:
        while True:
            time.sleep(1.0)
    except KeyboardInterrupt:
        gateway.stop()
    return 0


def cmd_configure(args: argparse.Namespace) -> int:
    """Inspects or modifies Claude Desktop configuration."""
    cfg_mgr = ClaudeConfigManager()
    path = cfg_mgr.config_path
    print(f"\n🛡️  MCP Guardian - Claude Desktop Configuration Manager")
    print(f"Config path: {path}")

    if not cfg_mgr.exists():
        print(f"Status: Configuration file does not exist yet at {path}")
        return 0

    if args.install:
        count, backup = cfg_mgr.wrap_all_servers()
        print(f"✅ Successfully wrapped {count} server(s) with MCP Guardian.")
        if backup:
            print(f"📁 Backup saved to: {backup}")
        return 0

    elif args.uninstall:
        count = cfg_mgr.unwrap_all_servers()
        print(f"✅ Successfully restored {count} server(s) to original configuration.")
        return 0

    # Default: status
    cfg = cfg_mgr.read_config()
    servers = cfg.get("mcpServers", {})
    print(f"\nConfigured Servers ({len(servers)}):")
    for s_name, s_data in servers.items():
        is_p = cfg_mgr.is_server_wrapped(s_data)
        status_str = "🛡️  PROTECTED by Guardian" if is_p else "⚠️  UNPROTECTED (Direct)"
        print(f" • {s_name}: {status_str}")
        print(f"   Command: {s_data.get('command')} {' '.join(s_data.get('args', []))[:60]}")
    print()
    return 0


def cmd_sample_server(args: argparse.Namespace) -> int:
    """Runs the mock sample MCP server."""
    from mcp_guardian.mock.sample_mcp_server import main
    main()
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="mcp-guardian",
        description="MCP Guardian: Real-time authorization proxy for Model Context Protocol communications.",
    )
    subparsers = parser.add_subparsers(dest="subcommand", help="Available subcommands")

    # wrap
    p_wrap = subparsers.add_parser("wrap", help="Wrap an MCP server stdio process for Claude Desktop")
    p_wrap.add_argument("--server-name", default="mcp_server", help="Identifiable name of the MCP server")
    p_wrap.add_argument("--ipc-port", type=int, default=None, help="Local IPC port for central daemon")
    p_wrap.add_argument("cmd", nargs=argparse.REMAINDER, help="Downstream server command to execute")

    # dashboard
    p_dash = subparsers.add_parser("dashboard", help="Launch live interactive dashboard and daemon")
    p_dash.add_argument("--ipc-port", type=int, default=8766, help="Local IPC port for wrapper communication")
    p_dash.add_argument("--enable-sse", action="store_true", help="Also start Claude Web SSE gateway")
    p_dash.add_argument("--sse-port", type=int, default=8765, help="Port for SSE gateway")

    # gateway
    p_gate = subparsers.add_parser("gateway", help="Run Claude Web SSE reverse proxy gateway")
    p_gate.add_argument("--host", default="127.0.0.1", help="Host interface to bind")
    p_gate.add_argument("--port", type=int, default=8765, help="Port to listen on")
    p_gate.add_argument("--name", default="web_mcp_server", help="Server name")
    p_gate.add_argument("--upstream", help="Command line of downstream MCP server to proxy to")

    # configure
    p_conf = subparsers.add_parser("configure", help="Inspect or patch Claude Desktop configuration")
    p_conf.add_argument("--install", action="store_true", help="Wrap all configured Claude Desktop servers")
    p_conf.add_argument("--uninstall", action="store_true", help="Restore original Claude Desktop servers")
    p_conf.add_argument("--status", action="store_true", help="View current protection status")

    # sample-server
    subparsers.add_parser("sample-server", help="Run built-in sample MCP server for testing")

    parsed_args = parser.parse_args(argv)

    if parsed_args.subcommand == "wrap":
        # Handle '--' separator if present in cmd
        if parsed_args.cmd and parsed_args.cmd[0] == "--":
            parsed_args.cmd = parsed_args.cmd[1:]
        return cmd_wrap(parsed_args)
    elif parsed_args.subcommand == "dashboard":
        return cmd_dashboard(parsed_args)
    elif parsed_args.subcommand == "gateway":
        return cmd_gateway(parsed_args)
    elif parsed_args.subcommand == "configure":
        return cmd_configure(parsed_args)
    elif parsed_args.subcommand == "sample-server":
        return cmd_sample_server(parsed_args)
    else:
        parser.print_help()
        return 0


if __name__ == "__main__":
    sys.exit(main())
