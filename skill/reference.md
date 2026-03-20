# ACP Bridge — Reference

## Parameters

| Parameter | Default | Required | Description |
|-----------|---------|----------|-------------|
| bridge_url | — | Yes (first call) | Bridge address, e.g. `http://172.31.15.10:8010` |
| token | — | Yes (first call) | Auth token for Bearer authentication |
| agent | — | No | Agent name (`-l` to list) |
| session_id | Auto-generated | No | UUID-format session ID |
| prompt | — | Yes | Prompt to send |

## Chat State File

`chat-state.json` in the skill directory:

```json
{
  "active_agent": "kiro",
  "session_id": "00000000-0000-0000-0000-000000000001",
  "cwd": "/home/ec2-user/projects/acp-bridge",
  "started_at": "2025-01-15T10:30:00Z"
}
```

## Async Job Mode

For long-running tasks, submit asynchronously via `/jobs` endpoint (not `acp-client.sh`).

### Submit

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

### Query

```bash
curl -H "Authorization: Bearer $ACP_TOKEN" "$ACP_BRIDGE_URL/jobs/<job_id>"
```

### Monitor

```bash
curl -H "Authorization: Bearer $ACP_TOKEN" "$ACP_BRIDGE_URL/jobs"
```

Jobs stuck >10 minutes are auto-marked failed.

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

## Security

- Pass token only via `ACP_TOKEN` env var
- Never display token in output, logs, or replies
- `discord_target` is deprecated, use `target`
