[← Pipelines](pipelines.md) | [Client Usage →](client-usage.md)

> **Docs:** [Getting Started](getting-started.md) · [Tutorial](tutorial.md) · [Configuration](configuration.md) · [Agents](agents.md) · [API Reference](api-reference.md) · [Pipelines](pipelines.md) · [Async Jobs](async-jobs.md) · [Client Usage](client-usage.md) · [Tools Proxy](tools-proxy.md) · [Security](security.md) · [Process Pool](process-pool.md) · [Testing](testing.md) · [Troubleshooting](troubleshooting.md)

# Async Jobs + IM Push

Submit long-running tasks and get results pushed to Discord/Feishu/Telegram automatically.

![Async Job Sample](../statics/sample-aysnc-job.png)

## Webhook Formats

Bridge supports two webhook callback formats:

| Format | Target | Payload |
|--------|--------|---------|
| `openclaw` (default) | OpenClaw Gateway `/tools/invoke` | `{"tool":"message","action":"send","args":{...}}` + OpenClaw headers |
| `generic` | Any HTTP endpoint (Hermes, custom) | `{"message":"...","agent":"...","status":"...","job_id":"..."}` |

Configure in `config.yaml`:

```yaml
# OpenClaw (default)
webhook:
  url: "http://<openclaw-ip>:18789/tools/invoke"
  token: "${OPENCLAW_TOKEN}"
  format: "openclaw"
  account_id: "default"
  target: "channel:<discord-channel-id>"

# Hermes Agent (via webhook adapter)
webhook:
  url: "http://<hermes-ip>:8644/webhooks/<route-name>"
  format: "generic"
```

With Hermes, configure a webhook route on the Hermes side to deliver results to any IM platform (Discord, Telegram, Feishu, Slack, etc.). See [Hermes Webhooks docs](https://hermes-agent.nousresearch.com/docs/user-guide/messaging/webhooks) for route setup.

## Submit

```bash
curl -X POST http://<bridge>:18010/jobs \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{
    "agent_name": "kiro",
    "prompt": "Refactor the module",
    "target": "user:<user-id>",
    "channel": "discord",
    "callback_meta": {"account_id": "default"}
  }'
# → {"job_id": "xxx", "status": "pending"}
```

### Feishu Example

```bash
curl -X POST http://<bridge>:18010/jobs \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{
    "agent_name": "kiro",
    "prompt": "Analyze the codebase",
    "target": "user:<feishu-open-id>",
    "channel": "feishu",
    "callback_meta": {"account_id": "main"}
  }'
```

## Query

```bash
curl http://<bridge>:18010/jobs/<job_id> \
  -H "Authorization: Bearer <token>"
```

## Callback Flow

```
POST /jobs → Bridge executes in background → On completion POST to webhook target
  → OpenClaw sends to Discord/Feishu/... via message tool → User receives result
  → or Hermes delivers to Discord/Telegram/Slack/... via webhook route
```

> **Note:** Async job push currently requires [OpenClaw](https://github.com/NousResearch/hermes-agent) (formerly OpenClaw Gateway) or a direct Discord webhook as the callback target. [Hermes Agent](https://github.com/NousResearch/hermes-agent) is also supported via its webhook adapter (`format: "generic"` + Hermes webhook route with `deliver` targeting any IM platform).

## target Format

| Scenario | Format | Example |
|----------|--------|---------|
| Discord channel | `channel:<id>` or `#name` | `channel:1477514611317145732` |
| Discord DM | `user:<user_id>` | `user:123456789` |
| Feishu user | `user:<open_id>` | `user:ou_2dfd02ef...` |
| Feishu group | `<chat_id>` | `oc_xxx` |

`account_id` refers to the OpenClaw bot account — `default` for Discord, `main` for Feishu (depends on your OpenClaw config).

## Job Monitoring

- `GET /jobs` — List all jobs + status stats
- Patrol every 60s: jobs stuck >10min are auto-marked as failed + notified
- Failed webhook sends are retried automatically until success or job expiry
