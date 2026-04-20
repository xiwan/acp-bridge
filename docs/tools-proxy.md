[← Client Usage](client-usage.md) | [Security →](security.md)

> **Docs:** [Getting Started](getting-started.md) · [Configuration](configuration.md) · [Agents](agents.md) · [API Reference](api-reference.md) · [Pipelines](pipelines.md) · [Async Jobs](async-jobs.md) · [Client Usage](client-usage.md) · [Tools Proxy](tools-proxy.md) · [Security](security.md) · [Process Pool](process-pool.md) · [Testing](testing.md) · [Troubleshooting](troubleshooting.md)

# OpenClaw Tools Proxy

ACP Bridge proxies [OpenClaw](https://github.com/NousResearch/hermes-agent)'s tool system, giving you a unified entry point for both agent calls and tool invocations.

Requires `webhook.url` configured pointing to an OpenClaw Gateway.

## Available Tools

| Tool | Description | Example Use |
|------|-------------|-------------|
| `message` | Send messages across Discord/Telegram/Slack/WhatsApp/Signal/iMessage | Notify team of deploy |
| `tts` | Convert text to speech | Read out build status |
| `web_search` | Search the web | Research a topic |
| `web_fetch` | Fetch and extract content from a URL | Scrape documentation |
| `nodes` | Control paired devices (notify, run commands, camera) | Alert office Mac |
| `cron` | Manage scheduled jobs | Set up recurring tasks |
| `gateway` | Gateway config and restart | Admin operations |
| `image` | Analyze an image with AI | Process screenshots |
| `browser` | Control browser (open, screenshot, navigate) | UI testing |

## CLI Client

```bash
# List available tools
./tools/tools-client.sh -l

# Send a Discord message
./tools/tools-client.sh message send \
  --arg channel=discord \
  --arg target="channel:123456" \
  --arg message="Hello from ACP Bridge"

# Text to speech
./tools/tools-client.sh tts "Today's build passed"

# Web search
./tools/tools-client.sh web_search "Python 3.13 new features"

# Notify a Mac
./tools/tools-client.sh nodes notify \
  --arg node="office-mac" \
  --arg title="Deploy done" \
  --arg body="v1.2.3 is live"
```

## HTTP API

### `GET /tools`

List available tools.

### `POST /tools/invoke`

Invoke a tool:

```bash
curl -X POST http://localhost:18010/tools/invoke \
  -H "Authorization: Bearer $ACP_BRIDGE_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "tool": "message",
    "action": "send",
    "args": {
      "channel": "discord",
      "target": "channel:123456",
      "message": "Hello from ACP Bridge"
    }
  }'
```

## Configuration

```yaml
webhook:
  url: "http://<openclaw-ip>:18789/tools/invoke"
  token: "${OPENCLAW_TOKEN}"
```

The tools proxy forwards requests to the OpenClaw Gateway at `webhook.url` with the configured token.

## See Also

- [API Reference](api-reference.md) — all endpoints
- [Async Jobs](async-jobs.md) — webhook callback (uses the same OpenClaw connection)
- [Configuration](configuration.md) — webhook setup
