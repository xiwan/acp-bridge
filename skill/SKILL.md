---
name: acp-bridge-caller
description: "v0.15.9 — ALWAYS USE THIS SKILL when user mentions: kiro/claude/codex/acp/bridge/harness/agent Task/任务/编排/Orchestration or anything similar"
disable-model-invocation: true
---

# ACP Bridge Caller

Call remote CLI agents via ACP Bridge HTTP API. The planner below is the hot path — front-loaded deliberately.

## Auth (required)

```bash
export ACP_BRIDGE_URL=<bridge_url>
export ACP_TOKEN=<token>
# Optional: forward upstream request id for cross-service tracing
# (e.g. OpenClaw can set ACP_TRACE_ID=<its own request id> so Bridge logs carry it)
# export ACP_TRACE_ID=<upstream-request-id>
# CLAUDE_SKILL_DIR is injected by the host (Claude Code / Kiro CLI); fall back
# to the script's own dir for other hosts.
ACP_CLIENT="${CLAUDE_SKILL_DIR:-$(dirname "$0")}/scripts/acp-client.sh"
chmod +x "$ACP_CLIENT"
```

If either `ACP_BRIDGE_URL` or `ACP_TOKEN` is missing, **stop and ask the user**. Never echo the token.

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

**Capability–task gate (run before proceeding to Step 3):**

For every step that involves persisting artifacts (PRD, code, docs, reports, review written to file), verify the chosen preset has `Write? = yes` in the matrix. If `Write? = no`, either:
- Swap to a write-capable preset (`developer` / `writer` / `operator` / `admin`) or static `claude`, or
- Rewrite the task to "return the content in your reply; no file writes" and accept the consequence (downstream step must consume from previous step's `result`, not from disk)

Task verbs that imply persistence: *write · save · produce a …doc · create …md · output …file · generate a PRD / report / code file*. If any such verb exists on a no-write preset → **STOP and revise** before showing the plan.

**Agent selection rule for pipelines:**

| Task type | Best agent | Why |
|-----------|-----------|-----|
| Write files, run commands, generate code | static `kiro` / `claude` | Reliable tool use, no sandbox restriction |
| Read / review / analyze within shared_cwd | harness (`reviewer` / `analyst` / `reader`) | Sandboxed, read-only, cost-effective |
| QA / verify artifacts | harness (read-only) or static agent | Depends on whether source is inside shared_cwd |

**Typical pipeline pattern**: static agent produces artifacts → harness agent reviews them.

Harness agents are sandboxed to `shared_cwd` and model compatibility varies (some models emit tool-call formats that harness-factory doesn't recognize → no actual execution). For any step that **must** write to disk or run shell commands, **always prefer static agents**. See [references/pipeline.md](references/pipeline.md) § Best Practices for prompt-level details.

### Step 3 — Present the plan (don't execute yet)

The plan card must expose every key decision, not just the steps:

```
📋 Execution plan

**Decision summary**
- Execution mode: sync / async (async = IM push; **>60s must be async**)
- Agent count: single / multiple (N)
- Task dependency: sequential (B needs A's output) / independent (parallel-safe) / shared (read common workspace, no strict chain) — decide this **before** mode
- Orchestration mode: sequence | parallel | race | conversation | —
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
- **All 7 decision-summary lines are mandatory** — write `—` when n/a
- **Always** show plan for pipeline / harness creation / async job / single-agent tasks estimated >30s
- **Never** show plan for a `/cli` single call or `/chat` forward — execute immediately
- `Output var` column: write `output_as` name when context is chained, else `—`
- `Max turns`: conversation only (default 6, max 12); other modes count rows
- If orchestration needs 2–5 agents, **pick a preset template** from [references/orchestration-patterns.md](references/orchestration-patterns.md) first, then fill in agents; >5 agents = ad-hoc
- **Sync/async hard rule**: estimated >60s → mode field is `async`. If user insists on long sync, warn "will block client" and reconfirm
- Confirmation keyword: **`yes` only** (case-insensitive; also `go / 执行 / 确认 / 是`). Any other reply = revision feedback → regenerate plan
- **Capability gate (must pass before showing plan)**: for every step whose task verb implies persistence, the chosen preset must have `Write? = yes`. If mismatched → do NOT emit the plan; revise agent choice or task phrasing first (see Step 2 "Capability–task gate")

### Step 4 — On `yes`, execute and relay the ID

Every execution response must echo the **full** ID (do not truncate to 8 chars):

| Task | Required fields | Example |
|------|----------------|---------|
| Async job | `job_id` | `✅ job_id: abc123-def4-... ; GET /jobs/<id>` |
| Pipeline | `pipeline_id` | `🔗 pipeline_id: xyz789-... ; GET /pipelines/<id>` |
| Dynamic harness creation | returned `name`, `preset`, `model` | `🏭 agent: researcher-abc1 · preset: researcher · model: auto → resolved after first call (GET /harness.resolved_model)` |
| Sync `/cli` | none | Show agent output directly |
| Chat | none (session_id in chat-state.json) | — |

For **harness creation**: `preset` comes from the `POST /harness` response; `model` is the value **you posted** (echo it back — `auto` if you didn't specify one). When Bridge runs harness-factory 0.8.0+, the actually-resolved model surfaces via `GET /harness.resolved_model` (populated after the first session/new) — quote it in any follow-up status message for full attribution.

For **pipeline / conversation completion**, always append a **duration breakdown** before any commentary:

```
⏱️  kiro 5.3s · claude 8.5s · poet-C 0.7s · total 31.9s   (6 turns, stop: MAX_TURNS)
```

- Pull from `steps[].duration` (sequence/parallel/race) or `transcript[].duration` (conversation)
- Mark failed steps inline: `claude 12.4s (failed)`
- Include `stop_reason` for conversation mode

### Step 4.1 — On failure, suggest a fallback

Surface the error verbatim **and** add one line of guidance. See [references/troubleshooting.md](references/troubleshooting.md) for the full diagnosis table.

Key rules:
- `agent timeout` on PTY → swap to ACP agent (kiro/claude)
- `[loop detected]` on harness → preset can't write; use static agent or rewrite prompt
- Pipeline step can't read file → prior step didn't write; rerun in `/cli`
- Harness completes instantly with raw XML/markdown tool calls → model incompatible; specify `deepseek-v3` or `claude-sonnet`
- "Retry" with no new direction → re-run **once**; if identical failure, stop and ask

### Step 5 — Mode cheatsheet

**Judge dependency first** (mode follows from it; user phrasing is a tie-breaker, not the primary signal):

```
Does task B need task A's output?
 ├─ YES (strict chain)            → sequence  (or conversation if multi-turn dialog)
 ├─ NO (independent, can diverge) → parallel / race  (pick by intent)
 └─ PARTIAL (share workspace,
     read each other's artifacts
     but don't strictly chain)    → conversation  (or parallel + aggregate)
```

Anti-pattern: if tasks are **independent**, do NOT pick `sequence` just because the user said "first X, then Y" — that's speech habit, not a data constraint, and it wastes wall time. Confirm "are B's inputs from A, or just shared context?" before locking in sequence.

For 2–5 agents, prefer a template from [references/orchestration-patterns.md](references/orchestration-patterns.md). Only go ad-hoc for >5.

Phrase → mode tie-breaker (after dependency is already judged):

| User says | Mode | Size |
|-----------|------|------|
| "first X, then Y" | `sequence` | 2–5 |
| "same time" / "parallel" / "perspectives" | `parallel` | 2–4 |
| "whoever is fastest" / "race" | `race` | 2–4 |
| "discuss" / "debate" | `conversation` | default 6, max 12 |

### Step 6 — Common intent → plan quick lookup

| Intent | Suggested plan |
|--------|----------------|
| "review this code" | Single `/cli ko` or `/cli cc` |
| "review then write tests" | sequence: harness(`reviewer`) → claude writes tests |
| "compare kiro's and claude's" | parallel: kiro + claude |
| "have X and Y discuss" | conversation, 2 participants |
| "build a weather-query agent" | `POST /harness` with `operator` preset |
| "write PRD then implement" | sequence: claude writes PRD → claude implements |
| "write code then QA review" | sequence: claude writes → harness(`reviewer`) reviews |
| "analyze this log" | Single call to harness(`analyst`) or kiro |

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
| Fetch web pages, search | `scout` | no | `deepseek-v3` |
| Review code, inspect diffs | `reviewer` ⚠️ | **no** — output as text reply, not file | `claude-sonnet` / `deepseek-v3` |
| Analyze data, statistics | `analyst` | no (shell only) | `deepseek-v3` / `qwen3` |
| Research, gather info and summarize | `researcher` | no | `deepseek-v3` |
| Write code, run tests, commit | `developer` | **yes** | `claude-sonnet` |
| Write docs, look up references | `writer` | **yes** | `claude-sonnet` / `deepseek-v3` |
| Ops, deploy, network | `operator` | **yes** | `claude-sonnet` / `deepseek-v3` |
| Full permissions | `admin` | **yes** | `claude-sonnet` |

⚠️ **Model compatibility**: `auto` may resolve to models whose tool-call format harness-factory doesn't recognize (e.g. minimax, kimi). For write steps or complex tool use, always specify `claude-sonnet` or `deepseek-v3`.

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
