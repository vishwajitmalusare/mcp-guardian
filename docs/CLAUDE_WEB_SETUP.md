# Claude Web (SSE) Integration Guide

Claude Web (claude.ai) connects to MCP servers over remote **Server-Sent Events (SSE)** and HTTP POST transports. MCP Guardian acts as a reverse proxy gateway, intercepting web-initiated requests and displaying authorization popups on your local desktop.

---

## 1. Starting the SSE Gateway

Run the gateway command, specifying the downstream MCP server you wish to expose:

```powershell
python -m mcp_guardian gateway --port 8765
```

By default, the gateway hosts the built-in sample server. You can also proxy any custom command:
```powershell
python -m mcp_guardian gateway --port 8765 --upstream "uvx mcp-server-sqlite --db-path my_data.db"
```

Console output:
```
=======================================================
🛡️  MCP Guardian SSE Gateway Active for Claude Web
=======================================================
 • SSE Endpoint URL:  http://127.0.0.1:8765/sse
 • Downstream Server: uvx mcp-server-sqlite --db-path my_data.db

Press Ctrl+C to terminate gateway.
```

---

## 2. Exposing the Gateway to Claude Web

Because Claude Web runs in the browser or on Anthropic cloud infrastructure, it requires a reachable HTTPS URL. You can expose your local port via a secure tunnel:

### Option A: Using Cloudflare Tunnels (Free & Fast)
```powershell
cloudflared tunnel --url http://localhost:8765
```
Cloudflare will provide an assigned HTTPS address, for example:
`https://random-subdomain.trycloudflare.com`

Your Claude Web SSE endpoint will be:
`https://random-subdomain.trycloudflare.com/sse`

### Option B: Using ngrok
```powershell
ngrok http 8765
```
Your Claude Web SSE endpoint will be:
`https://your-ngrok-domain.ngrok-free.app/sse`

---

## 3. Connecting in Claude Web / Remote Clients

In your Claude Web connector or remote MCP client:
1. Enter the public SSE URL (`https://.../sse`).
2. Claude Web will initiate the `GET /sse` stream and receive the message submission endpoint.
3. When Claude Web asks to call a tool, MCP Guardian pauses execution and presents the desktop confirmation modal on your screen with the **[Claude Web]** badge.
4. If you click **Allow**, the call proceeds and results are streamed back.
5. If you click **Block**, a synthetic error is returned, preventing downstream execution.
