# Async Jobs

## Submit

```bash
curl -X POST "$ACP_BRIDGE_URL/jobs" \
  -H "Authorization: Bearer $ACP_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "agent_name": "<agent>",
    "prompt": "<prompt>",
    "target": "<deliveryContext.to>",
    "channel": "discord",
    "callback_meta": { "account_id": "<deliveryContext.accountId>" }
  }'
```

Returns: `{"job_id": "xxx", "status": "pending"}`

Reply to user: "✅ Submitted. Results will be pushed automatically when done."

### Feishu

```bash
curl -X POST "$ACP_BRIDGE_URL/jobs" \
  -H "Authorization: Bearer $ACP_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "agent_name": "<agent>",
    "prompt": "<prompt>",
    "target": "user:<feishu-open-id>",
    "channel": "feishu",
    "callback_meta": { "account_id": "main" }
  }'
```

### With Custom Working Directory

Add `"cwd": "/path/to/project"` to the JSON body.

## Query

```bash
curl -H "Authorization: Bearer $ACP_TOKEN" "$ACP_BRIDGE_URL/jobs/<job_id>"
```

## Monitor

```bash
# Job list + status stats
curl -H "Authorization: Bearer $ACP_TOKEN" "$ACP_BRIDGE_URL/jobs"

# Pool stats (per-agent sessions, busy count)
curl -H "Authorization: Bearer $ACP_TOKEN" "$ACP_BRIDGE_URL/health/agents"
```

Jobs stuck >10 minutes are auto-marked failed. Pool health check runs every 60s
(ping idle connections, kill unresponsive, clean up past-TTL sessions, kill orphans).

## Target Format

| Scenario | Format | Example |
|----------|--------|---------|
| Discord channel | `channel:<id>` | `channel:1477514611317145732` |
| Discord DM | `user:<user_id>` | `user:123456789` |
| Feishu user | `user:<open_id>` | `user:ou_2dfd02ef...` |
| Feishu group | `<chat_id>` | `oc_xxx` |

## Callback Requirements

| Field | Source | Description |
|-------|--------|-------------|
| `target` | `deliveryContext.to` | Destination (see format above) |
| `channel` | IM platform | `discord`, `feishu`, etc. Default: `discord` |
| `callback_meta.account_id` | Bot account | `default` for Discord, `main` for Feishu |

Missing `target` → job runs but no push. Missing `account_id` → OpenClaw returns 500.
