# MCP Guardian: System Design Decisions & Architecture FAQ

This guide details the architectural decisions, trade-offs, security model, and implementation details of MCP Guardian.

---

## 1. System Abstract

> *"MCP Guardian is an authorization proxy and security firewall for Anthropic's Model Context Protocol (MCP). It intercepts tool invocation requests originating from Claude Desktop (via Stdio line-delimited JSON-RPC) and Claude Web (via HTTP/Server-Sent Events) before they reach the executing system. It presents an always-on-top confirmation modal displaying the target tool, parameter payloads, and automated risk scoring, giving the user explicit Allow/Block control over each action. On denial, it synthesizes an MCP-compliant error response so the LLM understands the rejection without breaking the conversational turn."*

---

## 2. Core Architectural Decisions & Trade-Offs

### A. Proxy Architecture vs. OS API Hooking
- **Option Considered**: Hooking into OS-level process pipes via DLL injection or ptrace.
- **Decision Taken**: Transparent wrapper proxy (`wrap` command).
- **Justification**:
  - MCP is designed around standard protocol transports (Stdio and SSE).
  - OS-level pipe hooking is brittle, OS-specific, easily blocked by antivirus or endpoint detection systems, and breaks with kernel updates.
  - A proxy wrapper is cross-platform (Windows, macOS, Linux), requires zero root/admin privileges, adheres strictly to the MCP specification, and produces reliable, portable behavior.

### B. Fail-Closed vs. Fail-Open Policy
- **Decision**: Fail-Closed (Default to Block).
- **Justification**:
  - In security-critical tool execution (where an AI agent can execute shell commands, format disks, or drop databases), the safe default must always be denial.
  - If a user dismisses the dialog, if the GUI fails to render, or if the 60-second confirmation timer elapses, the request is automatically blocked.

### C. Tool Error (`isError: true`) vs. JSON-RPC Protocol Error (`code: -32000`)
- **Decision**: Default to MCP Tool Result with `isError: True`.
- **Justification**:
  - In the MCP specification, returning a protocol-level JSON-RPC error indicates a transport or framing failure (which can cause Claude to abort the entire chat thread or report a client crash).
  - Returning a tool-level result with `isError: True` provides a structured error message (`"[MCP Guardian] Request blocked by user policy"`). Claude's model understands that the tool was denied by the user and can respond intelligently: *"The action was blocked by your policy; would you like me to try an alternative approach?"*

---

## 3. Technical Architecture & Implementation FAQ

### Q1: How does Claude Desktop know to route through your proxy?
**Answer:**
Claude Desktop uses a configuration file (`claude_desktop_config.json`) where each server specifies an executable `command` and an array of `args`.
Our tool provides an automated patcher (`python -m mcp_guardian configure --install`) that re-points the server entry to:
```json
{
  "command": "python",
  "args": ["-m", "mcp_guardian", "wrap", "--server-name", "filesystem", "--", "<original_command>", "<original_args>"]
}
```
Claude Desktop spawns our wrapper as the direct child process. Our wrapper then spawns the real server as its child, acting as a transparent man-in-the-middle on `stdin`/`stdout`.

---

### Q2: How does Claude Web connect to MCP, and how does your gateway capture its traffic?
**Answer:**
Claude Web cannot execute local desktop processes directly from the browser; instead, it uses the remote **HTTP/SSE transport** specified in the MCP protocol.
1. The client establishes a persistent HTTP connection to `GET /sse`.
2. The server responds with `Content-Type: text/event-stream` and emits an `endpoint` event specifying where messages should be posted (e.g. `/messages?sessionId=<uuid>`).
3. Claude Web submits JSON-RPC calls via HTTP POST to `/messages`.
4. Our `SSEGatewayServer` receives the POST, checks if the method is `tools/call`, triggers the desktop confirmation modal, and if approved, forwards the call to the downstream MCP server before returning the result over the active SSE stream.

---

### Q3: How do you prevent deadlocks when streaming `stdin` and `stdout` in Python?
**Answer:**
Pipes in Python have default buffer sizes (typically 4KB to 8KB on Windows/Linux). If both parent and child wait for buffers to fill, a deadlock occurs.
We prevent deadlocks using three mechanisms:
1. **Unbuffered Streaming**: We pass `env["PYTHONUNBUFFERED"] = "1"` when spawning child processes.
2. **Explicit Line Flushing**: We use `sys.stdout.flush()` and `proc.stdin.flush()` immediately after every line write.
3. **Dedicated Reader Threads**: We run separate daemon threads for reading `sys.stdin`, `proc.stdout`, and `proc.stderr`, each reading line-by-line using `.readline()`.

---

### Q4: How does MCP Guardian handle simultaneous parallel tool calls from the LLM?
**Answer:**
Modern LLMs can invoke multiple tools concurrently in a single turn.
1. When incoming messages arrive on `stdin` or via HTTP POST, the read loop immediately dispatches each message to a separate worker thread.
2. Each request is registered in `ConcurrencyManager` with its unique JSON-RPC `id` and a distinct `threading.Event`.
3. The UI presents each request in the pending queue.
4. When a user approves or blocks Request #2, Request #2 is released immediately with its matching `id`, while Request #1 and Request #3 remain pending until decided.
5. Thread locks around stream writers ensure that responses are never interleaved or corrupted.

---

### Q5: Why did you build a hybrid IPC (File Mailbox + Localhost Socket)?
**Answer:**
In modern enterprise environments, developer workstations often run under strict endpoint security, sandboxes, or group policies that restrict binding or connecting to local loopback TCP sockets (triggering Windows error code `10013`).
To guarantee 100% operational reliability:
- The daemon creates an atomic file mailbox in `.mcp_guardian/ipc/` with subfolders for `requests/` and `responses/`.
- Wrappers write atomic request files and poll for responses.
- If socket access is available, standard localhost TCP sockets can be used.
- If no central daemon is running at all, the wrapper gracefully falls back to displaying a standalone modal dialog directly.

---

### Q6: What is the purpose of the Risk Analyzer?
**Answer:**
In human-in-the-loop approval systems, "alert fatigue" is a major security risk: users often click "Allow" without reading raw JSON parameters.
Our Risk Analyzer runs pattern heuristics across tool names and argument payloads:
- Identifies destructive shell commands (`rm -rf`, `format`, `del /f`, fork bombs).
- Flags access to sensitive files (`.env`, `id_rsa`, `.aws/credentials`).
- Detects irreversible SQL operations (`DROP TABLE`, `TRUNCATE`).
The confirmation modal highlights these findings with a prominent **CRITICAL RISK** or **HIGH RISK** badge, explaining exactly why an operation warrants caution.

---

## 4. Key Terminology Checklist
- **JSON-RPC 2.0**: Protocol framing specification requiring `jsonrpc`, `id`, `method`, and `params`.
- **HITL (Human-in-the-Loop)**: Security pattern where automated actions require explicit human authorization.
- **Fail-Closed**: A system state where any unexpected failure or timeout defaults to the most secure option (Deny).
- **SSE (Server-Sent Events)**: One-way HTTP streaming standard used for server-to-client notifications and responses.
- **Alert Fatigue**: The psychological effect where operators approve alerts indiscriminately; mitigated through heuristic risk highlighting.
