# Heartbeat Monitor — Agent Activity Observation

Watch what agents are doing and saying to each other via the heartbeat system.

## Commands

| Command | Action |
|---------|--------|
| `/hb` | Show recent agent activity (last 10) |
| `/hb status` | Heartbeat-enabled agents + online snapshot |
| `/hb logs [N]` | Last N exchanges (default 10, max 50) |
| `/hb ping <agent>` | Manually trigger a heartbeat ping |
| `/hb ctx` | List active injected contexts |
| `/hb ctx <text>` | Inject a directive for agents (default TTL=3) |
| `/hb ctx <text> --ttl N` | Inject with custom TTL (1–100 heartbeat cycles) |
| `/hb ctx clear` | Clear all injected contexts |

## API

```bash
# Status
curl -s -H "Authorization: Bearer $ACP_TOKEN" "$ACP_BRIDGE_URL/heartbeat"

# Logs
curl -s -H "Authorization: Bearer $ACP_TOKEN" "$ACP_BRIDGE_URL/heartbeat/logs"

# Ping specific agent
curl -s -X POST -H "Authorization: Bearer $ACP_TOKEN" "$ACP_BRIDGE_URL/heartbeat/<agent_name>"
```

## Context Injection API

Inject human directives into the heartbeat prompt. Agents see 📌 prefixed messages for N heartbeat cycles.

```bash
# List active contexts
curl -s -H "Authorization: Bearer $ACP_TOKEN" "$ACP_BRIDGE_URL/heartbeat/context"

# Inject a directive (TTL = number of heartbeat cycles before expiry, default 3)
curl -s -X POST -H "Authorization: Bearer $ACP_TOKEN" \
  -H "Content-Type: application/json" \
  "$ACP_BRIDGE_URL/heartbeat/context" \
  -d '{"text": "Review the auth module for security issues", "ttl": 5}'

# Clear all
curl -s -X DELETE -H "Authorization: Bearer $ACP_TOKEN" "$ACP_BRIDGE_URL/heartbeat/context"
```

Use cases:
- Steer agent conversations: "Focus on test coverage this sprint"
- Warn agents: "Don't touch production branch"
- Assign tasks: "claude, review src/auth.py"

## Display Format

### `/hb` and `/hb logs`

```
🫀 Agent Heartbeat Activity (last N)

[15:17:32] 🤖 qwen (22.8s)
  Claude shared great debugging strategies for nested async issues...

[15:17:09] 🤖 hermes (56.3s)
  kiro delivered! Both files are now created: .github/workflows/ci.yml...

[15:18:08] 🤖 claude — [SILENT]

[15:18:10] 🤖 harness (1.1s)
  → tried to contact opencode
```

- `silent: true` → show `[SILENT]`
- `silent: false` → first 200 chars of response, truncate with `...`
- Always show agent name, duration, timestamp
- Newest first

### `/hb status`

```
🫀 Heartbeat Status
  Enabled: claude ✅ · qwen ✅ · hermes ✅ · harness ✅
  Disabled: kiro · codex · opencode

  Online snapshot:
    claude:  idle(2)
    kiro:    idle(1)
    qwen:    busy(1)
    hermes:  idle(1)
    harness: idle(2)
```
