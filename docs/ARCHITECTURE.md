# MCP Guardian: Architectural Specification

## 1. System Overview

MCP Guardian is an authorization proxy and security firewall designed for the **Model Context Protocol (MCP)** ecosystem. It provides human-in-the-loop (HITL) approval controls over outgoing tool calls initiated by AI clients, including **Claude Desktop** and **Claude Web**.

The architecture decouples protocol transport from authorization policy, allowing identical authorization, risk heuristics, audit logging, and modal alerts across different transport media.

```
                      +-------------------+
                      |   Claude Client   |
                      | (Desktop or Web)  |
                      +---------+---------+
                                |
             [JSON-RPC 2.0 over Stdio or SSE/HTTP]
                                |
                                v
                   +-------------------------+
                   |   MCP Guardian Proxy    |
                   |   (Stdio / SSE Gateway) |
                   +------------+------------+
                                |
              +-----------------+-----------------+
              |                                   |
    [Non-Tool Message]                   [Tool Call Message]
              |                                   |
              | (Fast-path)                       v
              |                         +-------------------+
              |                         |   Risk Analyzer   |
              |                         +---------+---------+
              |                                   |
              |                                   v
              |                         +-------------------+
              |                         | Confirmation Modal|
              |                         |  (Always-on-top)  |
              |                         +---------+---------+
              |                                   |
              |                    +--------------+--------------+
              |                    |                             |
              |                 [ALLOW]                       [BLOCK]
              |                    |                             |
              |                    v                             v
              +---------> +-----------------+           +-----------------+
                          | Downstream MCP  |           | Synthetic Error |
                          | Server Process  |           | Generated       |
                          +--------+--------+           +--------+--------+
                                   |                             |
                                   +--------------+--------------+
                                                  |
                                                  v
                                      +-----------------------+
                                      | Client Receives Reply |
                                      +-----------------------+
```

---

## 2. Supported Client Transports

### A. Claude Desktop (Stdio Man-in-the-Middle)
Claude Desktop runs local MCP servers by launching child processes and communicating over standard input and standard output (`stdio`) using line-delimited JSON-RPC 2.0.

- **Wrapping Mechanism**: Instead of pointing Claude Desktop directly to the target MCP server command, `claude_desktop_config.json` points to:
  ```json
  {
    "command": "python",
    "args": [
      "-m", "mcp_guardian", "wrap",
      "--server-name", "filesystem",
      "--",
      "npx", "-y", "@modelcontextprotocol/server-filesystem", "C:\\Users"
    ]
  }
  ```
- **Passthrough Phase**: Initialization (`initialize`, `notifications/initialized`), ping, and discovery (`tools/list`, `resources/list`) flow through without latency.
- **Interception Phase**: When a `tools/call` request arrives:
  1. The proxy parses the message and extracts the tool name and arguments.
  2. The proxy queries the local policy engine / IPC coordinator.
  3. An always-on-top modal appears on the operator's desktop.
  4. If **ALLOWED**, the raw request is flushed to the downstream server's `stdin`.
  5. If **BLOCKED**, the downstream process never receives the request. The proxy constructs a compliant MCP tool error response (`{"result": {"isError": true, "content": [...]}}`) and writes it directly back to Claude Desktop's `stdout`.

### B. Claude Web (HTTP/SSE Reverse Proxy Gateway)
Claude Web communicates with remote MCP servers via Server-Sent Events (SSE) and HTTP POST requests.

- **Endpoint Setup**: The proxy hosts an HTTP/SSE server at `http://127.0.0.1:8765/sse` (which can be exposed over an HTTPS tunnel like ngrok or cloudflared).
- **Session Flow**:
  1. Claude Web initiates a `GET /sse` connection.
  2. The gateway issues an initial SSE event containing the submission URI:
     ```
     event: endpoint
     data: /messages?sessionId=<uuid>
     ```
  3. Claude Web submits JSON-RPC calls via `POST /messages?sessionId=<uuid>`.
  4. If a tool invocation is submitted, the gateway halts execution and presents the desktop confirmation modal.
  5. Upon operator decision, the response (either the downstream server's execution result or a synthetic denial) is streamed back through the active SSE connection.

---

## 3. Concurrency and Synchronization Model

Modern LLMs frequently generate parallel tool calls within a single conversational turn. The synchronization engine manages concurrent requests as follows:

1. **Unique Request Tracking**: Every incoming request has a unique JSON-RPC identifier (`id`).
2. **Synchronization Primitives**: Each pending request is paired with an independent synchronization event (`threading.Event`).
3. **Non-Blocking I/O**: The main reading loop dispatches each incoming message to a worker thread, ensuring that a pending modal does not block the receipt of subsequent requests.
4. **Independent Resolution**: If Claude fires three simultaneous tool calls (e.g., Request A, Request B, Request C), three pending entries are registered in the queue. The operator can inspect and resolve each request independently or sequentially without race conditions.

---

## 4. Resilient Hybrid IPC Architecture

In multi-server environments, Claude Desktop spawns separate wrapper processes for each configured MCP server. A centralized UI coordinates all prompts through a resilient IPC mechanism:

- **Primary Transport (File Mailbox IPC)**: An atomic file-based mailbox exchange located in `.mcp_guardian/ipc/`.
  - Clients write atomic temporary files into `requests/req_<id>.tmp` and rename them to `.json`.
  - The central daemon processes the request and writes `responses/resp_<id>.json`.
  - **Advantages**: Completely independent of OS network configuration, immune to firewall restrictions, and functional within isolated sandbox environments.
- **Secondary Transport (Localhost TCP Socket)**: Used when socket access is permissible and active.
- **Autonomous Standalone Fallback**: If the central daemon is not running, the wrapper automatically detects the absence of a coordinator and opens an autonomous modal directly on the current process thread.

---

## 5. Security & Fail-Safe Design

| Threat / Scenario | Mitigation Strategy |
| :--- | :--- |
| **User Inaction / Timeout** | Default policy automatically triggers **BLOCK** after 60 seconds (Fail-Closed design). |
| **Window Dismissal (X button)** | Closing the modal without choosing Allow defaults to **BLOCK**. |
| **Destructive Command Injection** | Risk analyzer highlights dangerous patterns (`rm -rf`, `format`, `DROP TABLE`, shell execution) with a high-visibility badge. |
| **Downstream Process Crash** | Broken pipe conditions are caught gracefully, and a structured JSON-RPC error is returned to Claude. |
| **Audit Compliance** | Every request, payload, timestamp, latency metric, and user decision is recorded in both human-readable text and JSONL format. |
