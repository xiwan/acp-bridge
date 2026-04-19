# OpenClaw Tools Proxy

ACP Bridge proxies OpenClaw's tool system, giving you a unified entry point for both agent calls and tool invocations.

## Available Tools

| Tool | Description |
|------|-------------|
| `message` | Send messages across Discord/Telegram/Slack/WhatsApp/Signal/iMessage |
| `tts` | Convert text to speech |
| `web_search` | Search the web |
| `web_fetch` | Fetch and extract content from a URL |
| `nodes` | Control paired devices (notify, run commands, camera) |
| `cron` | Manage scheduled jobs |
| `gateway` | Gateway config and restart |
| `image` | Analyze an image with AI |
| `browser` | Control browser (open, screenshot, navigate) |

## Client Usage

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

## Direct API

```bash
curl -X POST http://<bridge>:18010/tools/invoke \
  -H "Authorization: Bearer <token>" \
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

Requires `webhook.url` to be configured pointing to an OpenClaw Gateway.
