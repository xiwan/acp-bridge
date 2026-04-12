---
name: acp-bridge-caller
description: "v0.11.3 — 通过 ACP Bridge HTTP API 调用远程 CLI agent，支持多 agent pipeline。Usage: /cli <prompt> | /cli ko <prompt> (kiro) | /cli cc <prompt> (claude) | /cli qw <prompt> (qwen) | /cli oc <prompt> (opencode) | /chat ko (进入对话模式)"
disable-model-invocation: true
---

# ACP Bridge Caller

Call remote CLI agents via [ACP Bridge](https://github.com/xiwan/acp-bridge/) HTTP API.

All calls go through `acp-client.sh` in this skill directory:

```bash
ACP_CLIENT="${CLAUDE_SKILL_DIR}/acp-client.sh"
chmod +x "$ACP_CLIENT"
```

## First Time Setup

If ACP Bridge is not yet installed or running, follow the full setup guide:
👉 [AGENT.md — Agent Execution Guide](https://github.com/xiwan/acp-bridge/blob/main/AGENT.md)

It covers environment diagnosis, installation, configuration, and service startup (Docker / systemd / nohup).

> Note: OpenClaw is optional — only needed for async job push (Discord/Feishu) and tools proxy. The core agent API (`/runs`, `/agents`) works standalone.

Once the Bridge is running and you can reach its `/health` endpoint, proceed below.

## Auth Setup

Before the first call, both `ACP_BRIDGE_URL` and `ACP_TOKEN` are required. If not set, **stop and ask the user**.

```bash
export ACP_BRIDGE_URL=<bridge_url>
export ACP_TOKEN=<token>
```

**Never display the token in plaintext.** If unauthorized, tell the user "the token may be incorrect".

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
| `/chat qw [--cwd <path>]` | Enter qwen chat mode |
| `/chat oc [--cwd <path>]` | Enter opencode chat mode |
| `/chat end` | Exit chat mode |
| `/chat status` | Show chat status |
| `/upload <file>` | `$ACP_CLIENT --upload "<file>"` |

## Message Routing

Matched by priority, first match wins:

1. Starts with `/chat` → Chat command
2. Starts with `/cli` → Single CLI call
3. `chat-state.json` exists and `active_agent` is set → Forward to current agent
4. Fallback → Normal processing (no Bridge call)

## Chat Mode

Once activated, subsequent messages are automatically forwarded to the agent without the `/cli` prefix.

**Activate**: `/chat ko` or `/chat cc [--cwd <path>]`
1. Generate a deterministic session_id (UUID v5, same as `/cli` mode)
2. Write `chat-state.json` (`active_agent`, `session_id`, `cwd`, `started_at`)
3. Reply: `🟢 Entered kiro chat mode (session: xxx)`

**Forward**: `$ACP_CLIENT -a <active_agent> -s <session_id> [--cwd <cwd>] "<user message>"`

**Exit**: `/chat end` → Delete `chat-state.json`, reply: `🔴 Exited chat mode`

**Status**: `/chat status` → Read `chat-state.json`, output agent, session, cwd, and uptime

**Switch**: Run `/chat cc` directly to replace state; the old session is preserved on the server.

## Session ID

- The script auto-generates a deterministic UUID (based on agent name); the same agent always reuses the same session
- Different agents use different session_ids to avoid conflicts
- Manual override: `$ACP_CLIENT -s <uuid> "<prompt>"`

## Pipeline (Multi-Agent Collaboration)

When the user asks multiple agents to work together, construct a pipeline API call.

### Recognition Rules

| User says | Mode | Example |
|-----------|------|---------|
| "先让 X 做...再让 Y 做..." / "X analyze, then Y review" | `sequence` | "让 kiro 分析代码，然后让 claude review" |
| "同时问 X 和 Y" / "ask X and Y together" | `parallel` | "同时让 kiro 和 claude 解释 ACP 协议" |
| "谁快用谁" / "whoever finishes first" | `race` | "让 kiro 和 claude 都算一下，谁快用谁" |
| "随便找一个" / "pick any agent" | `random` | "随便找个 agent 帮我看看这段代码" |
| "让 X 和 Y 讨论..." / "have X and Y discuss" | `conversation` | "让 kiro 和 claude 讨论微服务架构" |

### How to Call

```bash
# sequence / parallel / race / random
curl -s -X POST "$ACP_BRIDGE_URL/pipelines" \
  -H "Authorization: Bearer $ACP_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "mode": "<sequence|parallel|race|random>",
    "steps": [
      {"agent": "<name>", "prompt": "<task>", "output_as": "<var>"},
      {"agent": "<name>", "prompt": "based on: {{<var>}}"}
    ]
  }'

# conversation (multi-turn dialog)
curl -s -X POST "$ACP_BRIDGE_URL/pipelines" \
  -H "Authorization: Bearer $ACP_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "mode": "conversation",
    "participants": ["kiro", "claude"],
    "topic": "Review the auth module for security issues",
    "config": {"max_turns": 8, "stop_conditions": ["DONE", "CONSENSUS"]}
  }'
```

- `output_as` + `{{var}}`: pass one step's output to the next (sequence mode)
- If user doesn't specify agents, use all available from `$ACP_CLIENT -l`
- Supports both ACP and PTY agents (e.g. codex) — mix freely
- Poll `GET /pipelines/<id>` until status is `completed` or `failed`

### Reply Format

After submitting, reply: "🔗 Pipeline submitted (mode: sequence, agents: kiro → claude)"
After completion, show each step's result with agent name.

## Additional Resources

- For async jobs, API details, and target formats, see [reference.md](reference.md)
- For error troubleshooting, see [troubleshooting.md](troubleshooting.md)
