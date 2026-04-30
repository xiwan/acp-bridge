---
name: acp-bridge-caller
description: "v0.18.5 — ALWAYS USE THIS SKILL when user mentions: kiro/claude/codex/acp/bridge/harness/hermes/openclaw/agent Task/任务/编排/Orchestration or anything similar"
disable-model-invocation: true
---

# ACP Bridge Caller

Call remote CLI agents via ACP Bridge HTTP API.

## Auth (required)

```bash
export ACP_BRIDGE_URL=<bridge_url>
export ACP_TOKEN=<token>
ACP_CLIENT="${CLAUDE_SKILL_DIR:-$(dirname "$0")}/scripts/acp-client.sh"
chmod +x "$ACP_CLIENT"
```

If either `ACP_BRIDGE_URL` or `ACP_TOKEN` is missing, **stop and ask the user**. Never echo the token.

## Init Check (auto, first use only)

```bash
$ACP_CLIENT -l
```

Display as: `🌉 ACP Bridge connected — Agents: kiro ✅ · claude ✅ · hermes ✅ ...`

If fails → show error and stop. If succeeds → cache, skip on subsequent requests.

## Message Routing

First match wins:
1. `/hb` → Heartbeat monitor (see [references/heartbeat.md](references/heartbeat.md))
2. `/chat` → Chat command (see [references/chat.md](references/chat.md))
3. `/cli` → Single CLI call
4. `chat-state.json` exists → Forward to current agent
5. Fallback → Normal processing (no Bridge call)

## Planning (for multi-agent / pipeline / async tasks)

For natural-language tasks, classify → pick agents → present plan → execute. Full workflow: [references/planning.md](references/planning.md)

Quick rules:
- Single verb, one agent, ≤60s → `/cli xx "..."` directly, no plan needed
- Multiple agents / "first X then Y" / "discuss" → Pipeline, show plan first
- Estimated >60s → **must be async** (`POST /jobs`)
- Harness presets & model compatibility → [references/harness-presets.md](references/harness-presets.md)

## Output Rules — Faithful Relay

You are a messenger, not an editor.

- **MUST**: show agent output as-is (including errors), attribute with `🤖 <agent> replied:`, let user decide on failure
- **MUST NOT**: rewrite / summarize / complete the task yourself / mix analysis into the agent reply

Commentary allowed *after* full output, separated with `---` and prefixed `💬 My note:`.

## Commands (aliases)

`./cli <prompt>` (default agent) · `./cli ko|cc|cx|qw|oc|hm|hf "..."` (kiro / claude / codex / qwen / opencode / hermes / harness) · `/chat ko|cc [--cwd <path>]` (enter) · `/chat end|status` · `/upload <file>` · `/hb [status|logs|ping|ctx]` (heartbeat)

## References

- [references/planning.md](references/planning.md) — Full planning workflow (Steps 1–7)
- [references/harness-presets.md](references/harness-presets.md) — Preset capability matrix, model compatibility
- [references/heartbeat.md](references/heartbeat.md) — Heartbeat monitor, agent activity observation
- [references/chat.md](references/chat.md) — Chat mode lifecycle, session ID, state file
- [references/pipeline.md](references/pipeline.md) — Pipeline API, modes, conversation config
- [references/orchestration-patterns.md](references/orchestration-patterns.md) — Preset templates for 2–5 agent orchestration
- [references/async-jobs.md](references/async-jobs.md) — Async jobs, target format, callback, monitoring
- [references/troubleshooting.md](references/troubleshooting.md) — Error diagnosis
- [references/usage.md](references/usage.md) — LiteLLM usage tracking, token/cache stats
- [scripts/acp-client.sh](scripts/acp-client.sh) — Bash client used by `/cli` and `/chat`
- [AGENT.md](../AGENT.md) — First-time setup guide
