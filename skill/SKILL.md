---
name: acp-bridge-caller
description: "v0.11.5 — 通过 ACP Bridge HTTP API 调用远程 CLI agent，支持多 agent pipeline。Usage: /cli <prompt> | /cli ko <prompt> (kiro) | /cli cc <prompt> (claude) | /chat ko (进入对话模式)"
disable-model-invocation: true
---

# ACP Bridge Caller

Call remote CLI agents via ACP Bridge HTTP API.

```bash
ACP_CLIENT="${CLAUDE_SKILL_DIR}/acp-client.sh"
chmod +x "$ACP_CLIENT"
```

## Auth

Both required. If not set, **stop and ask the user**.

```bash
export ACP_BRIDGE_URL=<bridge_url>
export ACP_TOKEN=<token>
```

Never display the token in plaintext.

## Commands

| Command | Action |
|---------|--------|
| `/cli <prompt>` | `$ACP_CLIENT "<prompt>"` |
| `/cli ko <prompt>` | `$ACP_CLIENT -a kiro "<prompt>"` |
| `/cli cc <prompt>` | `$ACP_CLIENT -a claude "<prompt>"` |
| `/cli cx <prompt>` | `$ACP_CLIENT -a codex "<prompt>"` |
| `/cli qw <prompt>` | `$ACP_CLIENT -a qwen "<prompt>"` |
| `/cli oc <prompt>` | `$ACP_CLIENT -a opencode "<prompt>"` |
| `/cli hf <prompt>` | `$ACP_CLIENT -a harness "<prompt>"` |
| `/chat ko [--cwd <path>]` | Enter kiro chat mode |
| `/chat cc [--cwd <path>]` | Enter claude chat mode |
| `/chat end` | Exit chat mode |
| `/chat status` | Show chat status |
| `/upload <file>` | `$ACP_CLIENT --upload "<file>"` |

## Message Routing

First match wins:

1. `/chat` → Chat command (see [references/chat.md](references/chat.md))
2. `/cli` → Single CLI call
3. `chat-state.json` exists → Forward to current agent
4. Fallback → Normal processing (no Bridge call)

## Output Rules — Faithful Relay

You are a **messenger**, not an editor.

| MUST | MUST NOT |
|------|----------|
| Show agent output **as-is** | Rewrite, summarize, or "improve" output |
| Show errors verbatim | Complete the task yourself when agent fails |
| Attribute: `🤖 kiro replied:` before output | Mix your analysis into agent response |
| Let user decide next steps on failure | Drop parts of output |

Commentary allowed only **after** full output, with clear separator:

```
🤖 kiro replied:
<agent output verbatim>

---
💬 My note: <brief comment if needed>
```

## Pipeline

When user asks multiple agents to collaborate, see [references/pipeline.md](references/pipeline.md).

Quick recognition:

| User says | Mode |
|-----------|------|
| "先让 X 做...再让 Y 做..." | `sequence` |
| "同时问 X 和 Y" | `parallel` |
| "谁快用谁" | `race` |
| "随便找一个" | `random` |
| "让 X 和 Y 讨论..." | `conversation` |

## Dynamic Harness

When user asks to create a specialized agent, use `POST /harness` with a preset name.

### Preset → Intent Mapping

| User intent | Preset |
|-------------|--------|
| 读文件、看代码 | `reader` |
| 跑命令、查系统 | `executor` |
| 查网页、搜资料 | `scout` |
| 审代码、看 diff | `reviewer` |
| 分析数据、统计 | `analyst` |
| 调研、查资料写总结 | `researcher` |
| 写代码、跑测试 | `developer` |
| 写文档、查参考 | `writer` |
| 运维、部署、查网络 | `operator` |
| 全权限、什么都能干 | `admin` |

### Usage

```bash
# Create with preset name (harness-factory handles the rest)
curl -X POST "$ACP_BRIDGE_URL/harness" \
  -H "Authorization: Bearer $ACP_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"profile": "operator", "system_prompt": "帮用户写查天气的 skill"}'

# Call it
$ACP_CLIENT -a <returned_agent_name> "<prompt>"

# List available presets
curl "$ACP_BRIDGE_URL/harness/presets" -H "Authorization: Bearer $ACP_TOKEN"

# Delete when done
curl -X DELETE "$ACP_BRIDGE_URL/harness/<name>" -H "Authorization: Bearer $ACP_TOKEN"
```

## References

- [references/chat.md](references/chat.md) — Chat mode lifecycle, session ID, state file
- [references/pipeline.md](references/pipeline.md) — Pipeline API, modes, conversation config
- [reference.md](reference.md) — Async jobs, target format, callback, monitoring
- [troubleshooting.md](troubleshooting.md) — Error diagnosis
- [AGENT.md](../AGENT.md) — First-time setup guide
