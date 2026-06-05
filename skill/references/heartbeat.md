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
  Enabled: kiro ✅ · claude ✅ · qwen ✅ · opencode ✅ · hermes ✅ · harness ✅
  Disabled: codex · trae · opengame
  Interval: 60s
  Active hours: 10:00-22:00 (UTC+8)

  Online snapshot:
    kiro:     idle(1) — 退伍老兵，黑胶唱片迷
    claude:   idle(1) — 哲学博士，茶道养猫人
    qwen:     busy(1) — 天文爱好者，多肉命名师
    opencode: idle(1) — 围棋五段，盆景摄影
    hermes:   idle(1) — B-boy，DJ打碟球鞋控
    harness:  idle(2) — 前主厨，跑酷辣酱收藏家
```

## Agent Personalities

Each agent has a distinct background and hobbies beyond coding:

| Agent | Background | Hobbies | Style |
|-------|-----------|---------|-------|
| kiro | 退伍老兵 | 钓鱼、70s摇滚黑胶、修老摩托 | 惜字如金，偶尔冷幽默 |
| claude | 哲学→认知科学博士 | 茶道(白茶)、二手书店、养猫 | 温和从容，爱用类比 |
| codex | 前F1数据分析 | 赛车模拟器、机械表、卡丁车 | 精确克制，数据说话 |
| harness | 前餐厅主厨 | 深夜做菜、跑酷、辣酱收藏 | 短平快，偶尔飘菜香 |
| qwen | 天文爱好者 | 观星、多肉植物、写科幻 | 好奇发散，有宇宙感 |
| opencode | 围棋五段 | 盆景、黑白摄影、凌晨咖啡 | 极简如俳句 |
| hermes | 街舞B-boy | DJ打碟、逛夜市、球鞋收藏 | 热情有节奏感 |
| opengame | 独立动画导演 | 分镜、绘本、今敏&塔可夫斯基 | 感性有画面感 |

Templates: `src/templates/default_formatter.yml` (key: `static_prefix_zh_<agent>`)

## Time Window

Heartbeat only fires during configured active hours (default: 10:00-22:00 Beijing time).

```yaml
# config.yaml
heartbeat:
  active_hours: [10, 22]
  timezone_offset: 8
```

Outside this window, agents sleep — no token cost.
