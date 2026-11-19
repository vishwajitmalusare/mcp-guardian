# 🛡️ MCP Guardian: Human-in-the-Loop Security & Authorization Proxy for Model Context Protocol

[![Python 3.9+](https://img.shields.io/badge/python-3.9+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Protocol: MCP](https://img.shields.io/badge/Protocol-MCP%20JSON--RPC%202.0-brightgreen.svg)](https://modelcontextprotocol.io)
[![Zero External Dependencies](https://img.shields.io/badge/Dependencies-Zero%20(Standard%20Library)-success.svg)]()

> A Python-based security proxy that monitors Model Context Protocol (MCP) communication initiated by **Claude Desktop** and **Claude Web**, giving users explicit **Allow / Block** authorization over every individual tool invocation.

---

## 📋 Table of Contents
- [Overview](#-overview)
- [Architecture & How It Works](#-architecture--how-it-works)
- [Key Features](#-key-features)
- [Project Structure](#-project-structure)
- [Quick Start & Installation](#-quick-start--installation)
- [Running the Interactive Demo](#-running-the-interactive-demo)
- [Connecting with Claude Desktop](#-connecting-with-claude-desktop)
- [Connecting with Claude Web](#-connecting-with-claude-web)

---

## 🔍 Overview

AI models powered by the **Model Context Protocol (MCP)** can autonomously run terminal commands, execute SQL queries, modify configuration files, and delete data.

**MCP Guardian** acts as a proactive security gateway positioned directly between the AI client and local/remote MCP servers:
1. **Detects** every outgoing `tools/call` request before execution.
2. **Analyzes** parameters for destructive patterns (`rm -rf`, `DROP TABLE`, sensitive file reads).
3. **Displays** an always-on-top confirmation modal on your screen with request details and risk scoring.
4. **Enforces** the operator's decision:
   - **Allow**: Releases the request to the target server for execution.
   - **Block**: Prevents execution and returns a structured MCP error (`isError: true`) so the conversation continues smoothly.

---

## 🏗️ Architecture & How It Works

MCP Guardian supports both standard MCP transport mechanisms:

```
┌────────────────────────────────┐       ┌────────────────────────────────┐
│         Claude Desktop         │       │           Claude Web           │
│   (Stdio JSON-RPC 2.0 lines)   │       │     (HTTP / SSE Streaming)     │
└───────────────┬────────────────┘       └───────────────┬────────────────┘
                │                                        │
                v                                        v
┌────────────────────────────────┐       ┌────────────────────────────────┐
│     Stdio Wrapper (MITM)       │       │    HTTP / SSE Gateway Proxy    │
│    python -m mcp_guardian wrap │       │   python -m mcp_guardian gw    │
└───────────────┬────────────────┘       └───────────────┬────────────────┘
                │                                        │
                └───────────────────┬────────────────────┘
                                    │
                                    v
                     ┌──────────────────────────────┐
                     │     Hybrid IPC Channel       │
                     │  (Atomic Mailbox & Sockets)  │
                     └──────────────┬───────────────┘
                                    │
                                    v
                     ┌──────────────────────────────┐
                     │    Central Guardian Daemon   │
                     │    & Always-on-Top Modal     │
                     └──────────────┬───────────────┘
                                    │
                     ┌──────────────┴───────────────┐
                     │                              │
                [ ✅ ALLOW ]                   [ 🚫 BLOCK ]
                     │                              │
                     v                              v
        ┌─────────────────────────┐    ┌─────────────────────────┐
        │ Forward to Downstream   │    │ Synthetic MCP Error:    │
        │ MCP Server Process      │    │ "Blocked by user policy"│
        └─────────────────────────┘    └─────────────────────────┘
```

For detailed architectural diagrams and state transitions, see [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

---

## ✨ Key Features

- **Full Client Coverage**: Protects both **Claude Desktop** (stdio lines) and **Claude Web** (Server-Sent Events).
- **Always-on-Top Confirmation Popups**: High-visibility modal dialogs (`wm_attributes('-topmost', True)`) that appear above active IDEs, terminals, and browsers with sound notifications.
- **Heuristic Risk Analyzer**: Automatic threat profiling that tags requests with color-coded badges (`CRITICAL`, `HIGH`, `MEDIUM`, `LOW`, `SAFE`) based on commands and parameters.
- **Fail-Closed Security**: 60-second auto-block timeout and window dismissal fail-safe defaults to prevent unauthorized execution.
- **Concurrency & Parallel Tool Handling**: Handles multiple simultaneous tool calls from advanced LLMs using thread-safe synchronization primitives.
- **Live Monitoring Dashboard**: Interactive control center with real-time request metrics, live audit trail, payload inspection, and CSV/JSON export.
- **Zero External Dependencies**: Core proxy, GUI modals, and IPC run on standard Python 3.9+ with built-in Tkinter.
- **One-Command Claude Desktop Setup**: Automatic config inspector that discovers, backs up, and wraps Claude Desktop configurations.

---

## 📁 Project Structure

```
├── mcp_guardian/
│   ├── core/                    # Core protocol engine
│   │   ├── models.py            # MCPRequest, Decision, AuditEntry models
│   │   ├── parser.py            # JSON-RPC 2.0 parser & synthetic response builder
│   │   ├── policy.py            # Session rules & timeout policy engine
│   │   ├── manager.py           # Concurrency & synchronization manager
│   │   └── audit.py             # Structured audit logger (JSONL + text)
│   ├── gui/                     # GUI components
│   │   ├── popup.py             # Always-on-top confirmation modal dialog
│   │   ├── dashboard.py         # Live management dashboard & metrics console
│   │   └── theme.py             # Dark-theme color palette & typography
│   ├── transports/              # Transport interception layers
│   │   ├── stdio_wrapper.py     # Stdio MITM wrapper for Claude Desktop
│   │   └── sse_gateway.py       # HTTP/SSE reverse proxy for Claude Web
│   ├── daemon/                  # Central background services
│   │   ├── service.py           # Daemon service coordinator
│   │   └── ipc.py               # Resilient hybrid IPC (Mailbox + Sockets)
│   ├── utils/                   # Utilities & heuristics
│   │   ├── risk_analyzer.py     # Heuristic risk assessment engine
│   │   └── config_patcher.py    # Claude Desktop auto-configurator
│   ├── mock/                    # Test harnesses
│   │   ├── sample_mcp_server.py # Realistic MCP server (calc, files, shell)
│   │   └── mock_client.py       # Simulator mimicking Claude Desktop & Web
│   └── cli.py                   # Unified command-line interface
├── scripts/
│   └── run_demo.py              # Interactive test demonstration script
├── tests/                       # Complete automated unit & integration test suite
│   ├── test_parser.py
│   ├── test_policy.py
│   ├── test_concurrency.py
│   ├── test_stdio_wrapper.py
│   └── test_sse_gateway.py
├── docs/                        # Comprehensive documentation
│   ├── ARCHITECTURE.md          # In-depth architectural specification
│   ├── DESIGN_DECISIONS.md      # System design decisions, trade-offs & FAQ
│   ├── CLAUDE_DESKTOP_SETUP.md  # Step-by-step setup guide for Claude Desktop
│   └── CLAUDE_WEB_SETUP.md      # Step-by-step setup guide for Claude Web (SSE)
├── pyproject.toml               # Python package configuration
└── requirements.txt             # Requirements specification
```

---

## 🚀 Quick Start & Installation

### 1. Clone the Repository
```bash
git clone https://github.com/vishwajitmalusare/mcp-guardian.git
cd mcp-guardian
```

### 2. Verify Python Installation
MCP Guardian requires Python 3.9 or higher:
```bash
python --version
```

### 3. Run the Automated Test Suite
Run the test suite to verify all components:
```bash
python tests/test_parser.py
python tests/test_policy.py
python tests/test_concurrency.py
python tests/test_stdio_wrapper.py
python tests/test_sse_gateway.py
```
*(All 5 test suites pass with zero third-party dependencies).*

---

## 🎬 Running the Interactive Demo

You can experience the complete approval and blocking workflow **without needing Claude installed**:

```bash
python scripts/run_demo.py
```

### What the Demo Shows:
1. **Safe Tool Approval**: Claude Desktop requests `calculate_sum(a=42, b=58)`. The confirmation popup appears with a `SAFE OPERATION` badge. Click **[ Allow ]** to proceed.
2. **Destructive Tool Denial**: Claude Desktop requests `execute_command(command="rm -rf /")`. The popup displays a prominent **CRITICAL RISK** alert. Click **[ Block ]** to reject. The downstream server never receives the command.
3. **Claude Web SSE Interception**: Simulates Claude Web connecting via HTTP/SSE and triggering a desktop modal.

---

## 🖥️ Connecting with Claude Desktop

### Automated Setup (One Command)
Run the auto-patcher to protect all servers currently configured in Claude Desktop:
```bash
python -m mcp_guardian configure --install
```

### Inspect Configuration:
```bash
python -m mcp_guardian configure --status
```

### Launch the Central Monitoring Dashboard:
```bash
python -m mcp_guardian dashboard
```

Now start **Claude Desktop**. Whenever Claude attempts to execute a tool, a confirmation popup appears requesting your approval.

To restore your original configuration at any time:
```bash
python -m mcp_guardian configure --uninstall
```
*(See [docs/CLAUDE_DESKTOP_SETUP.md](docs/CLAUDE_DESKTOP_SETUP.md) for full details).*

---

## 🌐 Connecting with Claude Web

1. **Start the SSE Gateway**:
   ```bash
   python -m mcp_guardian gateway --port 8765
   ```
2. **Expose the Endpoint via Tunnel**:
   ```bash
   cloudflared tunnel --url http://localhost:8765
   # Or using ngrok:
   ngrok http 8765
   ```
3. In Claude Web or any remote MCP client, connect to:
   `https://<your-tunnel-url>/sse`

When Claude Web issues a tool call, the authorization popup will trigger on your desktop with the `[Claude Web]` badge.
*(See [docs/CLAUDE_WEB_SETUP.md](docs/CLAUDE_WEB_SETUP.md) for full details).*

---

## 📄 License
This project is licensed under the MIT License - see the LICENSE file for details.
