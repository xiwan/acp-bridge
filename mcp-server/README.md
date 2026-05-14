# ACP Bridge MCP Server

Expose your remote ACP Bridge agents as MCP tools — any MCP-compatible client can call them.

## Prerequisites

```bash
pip install mcp httpx
# or
uv pip install mcp httpx
```

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `ACP_BRIDGE_URL` | Yes | Bridge address (e.g. `http://your-ec2:18010`) |
| `ACP_BRIDGE_TOKEN` | Yes | Auth token |
| `ACP_TIMEOUT` | No | Sync call timeout in seconds (default: 300) |

## Client Configuration

### Claude Desktop

`~/.claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "acp-bridge": {
      "command": "python3",
      "args": ["/path/to/acp-bridge/mcp/server.py"],
      "env": {
        "ACP_BRIDGE_URL": "http://your-bridge:18010",
        "ACP_BRIDGE_TOKEN": "your-token"
      }
    }
  }
}
```

### Kiro

`.kiro/settings/mcp.json`:

```json
{
  "mcpServers": {
    "acp-bridge": {
      "command": "python3",
      "args": ["/path/to/acp-bridge/mcp/server.py"],
      "env": {
        "ACP_BRIDGE_URL": "http://your-bridge:18010",
        "ACP_BRIDGE_TOKEN": "your-token"
      }
    }
  }
}
```

### Cursor / VS Code

`.cursor/mcp.json` or VS Code MCP settings:

```json
{
  "mcpServers": {
    "acp-bridge": {
      "command": "python3",
      "args": ["/path/to/acp-bridge/mcp/server.py"],
      "env": {
        "ACP_BRIDGE_URL": "http://your-bridge:18010",
        "ACP_BRIDGE_TOKEN": "your-token"
      }
    }
  }
}
```

## Available Tools

| Tool | Description |
|------|-------------|
| `acp_list_agents` | List all available agents |
| `acp_call` | Call an agent synchronously |
| `acp_submit_job` | Submit async background job |
| `acp_job_status` | Query job status/result |
| `acp_pipeline` | Run multi-agent pipeline |
| `acp_pipeline_status` | Query pipeline status |
| `acp_invoke_tool` | Call OpenClaw tools (message, browser, etc.) |

## Examples

Once configured, your AI client can use these tools naturally:

- "List my available agents" → calls `acp_list_agents`
- "Ask kiro to fix the bug in utils.py" → calls `acp_call(agent="kiro", prompt="...")`
- "Run a pipeline: kiro reviews, then claude implements" → calls `acp_pipeline(...)`
- "Send a Discord message to #general" → calls `acp_invoke_tool(tool="message", action="send", ...)`

## Architecture

```
MCP Client (Claude Desktop / Kiro / Cursor)
    ↕ stdio JSON-RPC
mcp/server.py (this file)
    ↕ HTTP
Remote ACP Bridge (your-ec2:18010)
    ↕ JSON-RPC / PTY
CLI Agents (Kiro, Claude, Codex, Harness, ...)
```
