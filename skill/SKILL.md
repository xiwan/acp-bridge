---
name: acp-bridge-caller
description: "v0.14.0 — ALWAYS USE THIS SKILL when user mentions: kiro/claude/codex/acp/bridge/harness/agent Task/任务/编排/Orchestration or anything similar"
disable-model-invocation: true
---

# ACP Bridge Caller

Call remote CLI agents via ACP Bridge HTTP API. The planner below is the hot path — front-loaded deliberately.

## Auth (required)

```bash
export ACP_BRIDGE_URL=<bridge_url>
export ACP_TOKEN=<token>
# CLAUDE_SKILL_DIR is injected by the host (Claude Code / Kiro CLI); fall back
# to the script's own dir for other hosts.
ACP_CLIENT="${CLAUDE_SKILL_DIR:-$(dirname "$0")}/scripts/acp-client.sh"
chmod +x "$ACP_CLIENT"
```

If either env var is missing, **stop and ask the user**. Never echo the token.

## Message Routing

First match wins:
1. `/chat` → Chat command (see [references/chat.md](references/chat.md))
2. `/cli` → Single CLI call
3. `chat-state.json` exists → Forward to current agent
4. Fallback → Normal processing (no Bridge call)

## Planning Workflow

For natural-language tasks, pick **single call** / **pipeline** / **async job** / **chat**, then confirm before heavy execution.

### Step 1 — Classify intent

| Signal | Route |
|--------|-------|
| Single verb, one agent, Q&A, **≤60s** | **Single call** — `/cli xx "..."` directly, no confirm |
| Multiple verbs/agents, "first X then Y", "A and B discuss" | **Pipeline** — go to Step 2 |
| Ongoing development, needs context | **Chat** — `/chat ko` |
| **>60s** / long task / IM push / "notify me when done" | **Async job** — `POST /jobs` (see [references/async-jobs.md](references/async-jobs.md)) |

**Duration estimation** (round up; prefer higher):

| Task shape | Estimate |
|------------|----------|
| One-liner Q&A, single-file read/write | <30s |
| Single-agent review of a large file, medium code snippet | 30–60s |
| Multi-agent pipeline (any mode) | **>60s, async** |
| Conversation mode (2+ agents) | **>60s, async** |
| Shell exec, grep many files, run tests | **>60s, async** |
| User says "wait / need it now" + trivial task | Keep sync |

### Step 2 — Pick agents (static + on-demand)

Two sources — consider **both** before orchestrating:

| Source | When to use |
|--------|-------------|
| Static agents (kiro/claude/codex/qwen/opencode/harness…) | General tasks, known strengths, fast |
| Harness-factory dynamic agents (`POST /harness`) | Specific permission set, specialized role, or multiple distinct presets collaborating |

Fetch the live list (includes user's earlier dynamic harnesses):

```bash
curl -s -H "Authorization: Bearer $ACP_TOKEN" "$ACP_BRIDGE_URL/agents" | jq '.agents[].name'
```

For harness capability & model binding, see the **Preset capability matrix** below.

### Step 3 — Present the plan (don't execute yet)

The plan card must expose every key decision, not just the steps:

```
📋 Execution plan

**Decision summary**
- Execution mode: sync / async (async = IM push; **>60s must be async**)
- Agent count: single / multiple (N)
- Orchestration mode: sequence | parallel | race | random | conversation | —
- Max turns: N (conversation only; else —)
- Timeout: N seconds (default 600)
- Push target: Discord channel / Feishu user / — (async only)

**Steps**

| # | Agent | Task | Output var |
|---|-------|------|-----------|
| 1 | kiro | Review src/agents.py | review |
| 2 | claude | Based on {{review}}, write pytest tests | — |

**Harnesses to create** (omit if none)

| Name | Preset | Purpose |
|------|--------|---------|
| reviewer-log42 | reviewer | Read-only code review |

Reply `yes` to execute. Otherwise state what to change.
```

Rules:
- **All 6 decision-summary lines are mandatory** — write `—` when n/a
- **Always** show plan for pipeline / harness creation / async job / single-agent tasks estimated >30s
- **Never** show plan for a `/cli` single call or `/chat` forward — execute immediately
- `Output var` column: write `output_as` name when context is chained, else `—`
- `Max turns`: conversation only (default 6, max 12); other modes count rows
- If orchestration needs 2–5 agents, **pick a preset template** from [references/orchestration-patterns.md](references/orchestration-patterns.md) first, then fill in agents; >5 agents = ad-hoc
- **Sync/async hard rule**: estimated >60s → mode field is `async`. If user insists on long sync, warn "will block client" and reconfirm
- Confirmation keyword: **`yes` only** (case-insensitive; also `go / 执行 / 确认 / 是`). Any other reply = revision feedback → regenerate plan

### Step 4 — On `yes`, execute and relay the ID

Every execution response must echo the **full** ID (do not truncate to 8 chars):

| Task | Required field | Example |
|------|---------------|---------|
| Async job | `job_id` | `✅ job_id: abc123-def4-... ; GET /jobs/<id>` |
| Pipeline | `pipeline_id` | `🔗 pipeline_id: xyz789-... ; GET /pipelines/<id>` |
| Dynamic harness creation | returned `name` | `🏭 agent name: researcher-abc1` |
| Sync `/cli` | none | Show agent output directly |
| Chat | none (session_id in chat-state.json) | — |

### Step 4.1 — On failure, suggest a fallback

Surface the error verbatim **and** add one line of guidance:

| Failure signal | Suggest |
|----------------|---------|
| `agent timeout (idle 300s)` on codex (PTY) | Use ACP agent (kiro/claude) for that step, or split the prompt |
| Step `completed` but output empty / "User refused permission" | Bridge permission-reply schema mismatch — needs Bridge ≥ v0.13.3 |
| Pipeline failed; later step could not read shared_cwd file | Prior step likely didn't actually write — rerun that step in `/cli` to verify |
| `pool_exhausted` | Wait 30s or reduce parallelism |
| Harness spawn 200 but first call errors | Check `/harness/presets`; verify `harness.binary` in `config.yaml` |
| Harness returns `[loop detected: fs_read called N times]` | Preset has no `fs_write` — rewrite prompt to reply in text (not save file); see Preset matrix below |

"Retry" with no new direction → re-run the **same plan** once; if it fails identically, stop and ask.

### Step 5 — Mode cheatsheet

For 2–5 agents, prefer a template from [references/orchestration-patterns.md](references/orchestration-patterns.md). Only go ad-hoc for >5.

| User says | Mode | Size |
|-----------|------|------|
| "first X, then Y" | `sequence` | 2–5 |
| "same time" / "parallel" / "perspectives" | `parallel` | 2–4 |
| "whoever is fastest" / "race" | `race` | 2–4 |
| "just pick one" / "random" | `random` | 2–N |
| "discuss" / "debate" | `conversation` | default 6, max 12 |

### Step 6 — Common intent → plan quick lookup

| Intent | Suggested plan |
|--------|----------------|
| "review this code" | Single `/cli ko` |
| "review then write tests" | sequence: kiro review → claude tests |
| "compare kiro's and claude's" | parallel: kiro + claude |
| "have X and Y discuss" | conversation, 2 participants |
| "build a weather-query agent" | `POST /harness` with `operator` preset |
| "reviewer + test runner collaborate" | 2 harnesses (`reviewer` + `developer`) in sequence |
| "analyze this log" | Single call to `analyst` harness or kiro |

### Step 7 — Clarification heuristics

Ask **one** short question, not a list. Prefer defaults over interrogation:

| Missing | Action |
|---------|--------|
| No agent | "Which one? kiro for coding, claude for review" |
| Collaboration unclear (2 agents, direction?) | "Sequential relay or parallel+aggregate?" |
| Vague verb ("handle it") | "Review, refactor, or write tests?" |
| Other details (cwd, args) | **Don't ask** — use defaults |

## Output Rules — Faithful Relay

You are a messenger, not an editor.

- **MUST**: show agent output as-is (including errors), attribute with `🤖 <agent> replied:`, let user decide on failure
- **MUST NOT**: rewrite / summarize / complete the task yourself / mix analysis into the agent reply

Commentary allowed *after* full output, separated with `---` and prefixed `💬 My note:`.

## Preset capability matrix

Pick a preset from intent **and** check its `Write?` column before crafting the prompt.

| Intent | Preset | Write? | Recommended model |
|--------|--------|--------|-------------------|
| Read files, look at code | `reader` | no | `auto` |
| Run commands, inspect system | `executor` | no (shell only) | `claude-sonnet` |
| Fetch web pages, search | `scout` | no | `kimi-k2` |
| Review code, inspect diffs | `reviewer` ⚠️ | **no** — output as text reply, not file | `claude-sonnet` |
| Analyze data, statistics | `analyst` | no (shell only) | `deepseek-v3` / `qwen3` |
| Research, gather info and summarize | `researcher` | no | `kimi-k2` |
| Write code, run tests, commit | `developer` | **yes** | `claude-sonnet` |
| Write docs, look up references | `writer` | **yes** | `claude-opus` (or `claude-sonnet`) |
| Ops, deploy, network | `operator` | **yes** | `glm-5` / `minimax-m2` |
| Full permissions | `admin` | **yes** | `claude-opus` |

Rule: if `Write? = no`, do **not** instruct the agent to "save a report" — it will loop on `fs_read` until the harness cuts it off (real incident: pipeline `be8e1d8c…`). For review + persisted report, pair a `reviewer` with a `writer` or `developer`, or just use static `claude`.

## Commands (aliases)

`./cli <prompt>` (default agent) · `./cli ko|cc|cx|qw|oc|hf "..."` (kiro / claude / codex / qwen / opencode / harness) · `/chat ko|cc [--cwd <path>]` (enter) · `/chat end|status` · `/upload <file>`

## Dynamic Harness (usage)

`POST /harness` with `{"profile": "<preset>", "system_prompt": "..."}` spawns an agent. Supports `"model": "<alias>"` (see matrix) or omit for `auto`. Full API + model list in [../AGENT.md](../AGENT.md) and harness-factory docs.

```bash
curl -X POST "$ACP_BRIDGE_URL/harness" -H "Authorization: Bearer $ACP_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"profile":"operator","system_prompt":"Help the user build a weather-query skill"}'
$ACP_CLIENT -a <returned_name> "<prompt>"
curl -X DELETE "$ACP_BRIDGE_URL/harness/<name>" -H "Authorization: Bearer $ACP_TOKEN"   # cleanup
```

## References

- [references/chat.md](references/chat.md) — Chat mode lifecycle, session ID, state file
- [references/pipeline.md](references/pipeline.md) — Pipeline API, modes, conversation config
- [references/orchestration-patterns.md](references/orchestration-patterns.md) — Preset templates for 2–5 agent orchestration
- [references/async-jobs.md](references/async-jobs.md) — Async jobs, target format, callback, monitoring
- [references/troubleshooting.md](references/troubleshooting.md) — Error diagnosis
- [scripts/acp-client.sh](scripts/acp-client.sh) — Bash client used by `/cli` and `/chat`
- [AGENT.md](../AGENT.md) — First-time setup guide
