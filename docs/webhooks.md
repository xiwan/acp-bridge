[← Async Jobs](async-jobs.md) | [Client Usage →](client-usage.md)

> **Docs:** [Getting Started](getting-started.md) · [Tutorial](tutorial.md) · [Configuration](configuration.md) · [Agents](agents.md) · [API Reference](api-reference.md) · [Pipelines](pipelines.md) · [Async Jobs](async-jobs.md) · **Webhooks** · [Client Usage](client-usage.md) · [Tools Proxy](tools-proxy.md) · [Security](security.md) · [Process Pool](process-pool.md) · [Testing](testing.md) · [Troubleshooting](troubleshooting.md)

# Webhooks

When an async job or pipeline completes, Bridge POSTs the result to a webhook URL. This is how agent results reach IM platforms (Discord, Feishu, Telegram, Slack, etc.).

## Two Formats, Two Protocols

Bridge supports two webhook formats because the downstream receivers speak different protocols:

| Format | Receiver | Payload | Auth |
|--------|----------|---------|------|
| `openclaw` | OpenClaw Gateway `/tools/invoke` | RPC: `{"tool":"message","action":"send","args":{...}}` | Bearer token |
| `generic` | Any HTTP endpoint (Hermes, custom) | Plain JSON: `{"job_id":"...","message":"...","status":"..."}` | HMAC-SHA256 |

**Why not unify?** OpenClaw expects its own RPC envelope (`tool` + `action` + `args`). Generic receivers (Hermes, custom webhooks) expect plain JSON with the message in a `message` field. Sending RPC format to a generic receiver works at the HTTP level (202 Accepted) but the receiver treats the entire JSON structure as raw text instead of extracting the message — the result is garbled output in your IM channel.

## Configuration

### OpenClaw

```yaml
webhook:
  url: "http://<openclaw-ip>:18789/tools/invoke"
  token: "${OPENCLAW_TOKEN}"
  format: "openclaw"
  account_id: "default"
  target: "channel:<discord-channel-id>"
```

### Hermes Agent

```yaml
webhook:
  url: "http://<hermes-ip>:8644/webhooks/acp-result"
  secret: "${HERMES_WEBHOOK_SECRET}"
  format: "generic"
  account_id: "default"
  target: "channel:<discord-channel-id>"
```

### Fields

| Field | Required | Description |
|-------|----------|-------------|
| `url` | Yes | Webhook endpoint URL |
| `token` | No | Bearer token for OpenClaw auth |
| `secret` | No | HMAC-SHA256 signing secret for generic auth |
| `format` | No | `"openclaw"` (default) or `"generic"` |
| `account_id` | No | Bot account identifier (sent as `x-openclaw-account-id` header) |
| `target` | No | Default push target (e.g. `channel:123456`, `user:789`) |

## Authentication: token vs secret

These are two different security mechanisms, not interchangeable names:

| Field | Mechanism | How it works | When to use |
|-------|-----------|-------------|-------------|
| `token` | Bearer token | Sent as `Authorization: Bearer <token>` header. Receiver checks the token value. | OpenClaw (internal service, simple auth) |
| `secret` | HMAC-SHA256 | Bridge computes `HMAC-SHA256(secret, request_body)` and sends the hex digest as `X-Webhook-Signature`. Receiver recomputes and compares. | Hermes, GitHub-style webhooks (integrity verification) |

Set one or the other, not both. If `secret` is set, it takes priority. If neither is set, no auth header is sent.

### HMAC Verification (receiver side)

```python
import hmac, hashlib

def verify(body: bytes, signature: str, secret: str) -> bool:
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)

# signature = request.headers["X-Webhook-Signature"]
```

## Payload Examples

### openclaw format

```json
{
  "tool": "message",
  "action": "send",
  "args": {
    "channel": "discord",
    "target": "channel:1234567890",
    "message": "✅ **kiro** completed job `abc123`\n\nHere is the result..."
  }
}
```

Headers: `Authorization: Bearer <token>`, `x-openclaw-account-id: default`, `x-openclaw-message-channel: discord`

### generic format

```json
{
  "job_id": "abc123",
  "agent": "kiro",
  "status": "completed",
  "message": "Here is the result...",
  "part": 1,
  "total_parts": 1
}
```

Headers: `X-Webhook-Signature: <hmac-hex>` (if `secret` is set)

## Per-request Override

Jobs and pipelines can override the global webhook config per request via `callback_meta`:

```bash
curl -X POST http://<bridge>:18010/jobs \
  -H "Authorization: Bearer $ACP_BRIDGE_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "agent_name": "kiro",
    "prompt": "Refactor the module",
    "target": "channel:9999999",
    "callback_meta": {
      "format": "generic",
      "secret": "per-request-secret",
      "account_id": "alt-bot"
    }
  }'
```

## Message Chunking

Long results are auto-chunked at 1800 characters (Discord safety limit). Each chunk is sent as a separate POST with `part` and `total_parts` fields, with a 0.5s delay between chunks.

## Retry Behavior

- Failed webhook sends are retried automatically on the next patrol cycle (every 60s)
- Retry count is tracked per job (`retries` field)
- Jobs with unsent webhooks are recovered on Bridge restart

## Data Flow

```
                    ┌─── openclaw ──► OpenClaw Gateway ──► Discord/Feishu
POST /jobs ──► Bridge executes ──┤
                    └─── generic ──► Hermes Agent ──► Discord/Telegram/Slack/...
```

## Hermes Side Setup

On the Hermes side, configure a webhook route in `~/.hermes/config.yaml`:

```yaml
platforms:
  webhook:
    extra:
      routes:
        acp-result:
          secret: "same-secret-as-bridge"
          deliver:
            platform: discord
            channel_id: "1234567890"
```

The `secret` must match the Bridge's `HERMES_WEBHOOK_SECRET`. See [Hermes Webhooks docs](https://hermes-agent.nousresearch.com/docs/user-guide/messaging/webhooks) for full route configuration.

## See Also

- [Async Jobs](async-jobs.md) — submitting background tasks
- [Pipelines](pipelines.md) — multi-agent orchestration with webhook push
- [Configuration](configuration.md) — full config.yaml reference
- [Security](security.md) — auth model and deployment recommendations
