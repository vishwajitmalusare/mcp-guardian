# Claude Desktop Integration Guide

This guide walks you through configuring Claude Desktop to route MCP tool calls through MCP Guardian.

---

## 1. Automated Configuration (Recommended)

MCP Guardian includes a built-in configuration utility that discovers, backs up, and updates your Claude Desktop configuration file automatically.

### Step 1: Run the Automatic Patcher
```powershell
python -m mcp_guardian configure --install
```

### Step 2: Check Protection Status
```powershell
python -m mcp_guardian configure --status
```
Example output:
```
🛡️  MCP Guardian - Claude Desktop Configuration Manager
Config path: C:\Users\<Username>\AppData\Roaming\Claude\claude_desktop_config.json

Configured Servers (2):
 • filesystem: 🛡️  PROTECTED by Guardian
   Command: python -m mcp_guardian wrap --server-name filesystem -- ...
 • sqlite: 🛡️  PROTECTED by Guardian
   Command: python -m mcp_guardian wrap --server-name sqlite -- ...
```

### To Revert at Any Time:
```powershell
python -m mcp_guardian configure --uninstall
```
*(Your original configuration is restored from the timestamped backup).*

---

## 2. Manual Configuration

If you prefer to configure Claude Desktop manually, open your configuration file:

### Configuration File Locations:
- **Windows**: `%APPDATA%\Claude\claude_desktop_config.json`
- **macOS**: `~/Library/Application Support/Claude/claude_desktop_config.json`
- **Linux**: `~/.config/Claude/claude_desktop_config.json`

### Example Configuration:

#### Before (Direct MCP Connection):
```json
{
  "mcpServers": {
    "sqlite": {
      "command": "uvx",
      "args": ["mcp-server-sqlite", "--db-path", "test.db"]
    }
  }
}
```

#### After (Protected by MCP Guardian):
```json
{
  "mcpServers": {
    "sqlite": {
      "command": "python",
      "args": [
        "-m", "mcp_guardian", "wrap",
        "--server-name", "sqlite",
        "--",
        "uvx", "mcp-server-sqlite", "--db-path", "test.db"
      ]
    }
  }
}
```

---

## 3. Running the Dashboard (Optional but Recommended)

You can launch the central monitoring dashboard before starting Claude Desktop:
```powershell
python -m mcp_guardian dashboard
```
- When Claude Desktop starts, all protected servers route authorization requests to this dashboard.
- If the dashboard is not running, each server wrapper will display confirmation popups independently as needed.
