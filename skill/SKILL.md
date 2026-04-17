---
name: acp-bridge-caller
description: "v0.13.6 — ALWAYS USE THIS SKILL when user mentions: kiro/claude/codex/acp/bridge/harness/agent Task/任务/编排/Orchestration or anything similar"
disable-model-invocation: true
---

# ACP Bridge Caller

Call remote CLI agents via ACP Bridge HTTP API.

```bash
# CLAUDE_SKILL_DIR is injected by the host (Claude Code / Kiro CLI) and points
# to this skill's installed directory. For other hosts, set it manually or
# replace with the absolute path to scripts/acp-client.sh.
ACP_CLIENT="${CLAUDE_SKILL_DIR:-$(dirname "$0")}/scripts/acp-client.sh"
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

## Planning Workflow

When the user describes a task in natural language, decide between **single call** and **pipeline**, then confirm before executing.

### Step 1 — Classify intent

| Signal | Route |
|--------|-------|
| Single verb, specific agent, one-shot Q&A, **estimated ≤60s** | **Single call** — run `/cli xx "..."` directly, no confirmation needed |
| Multiple verbs / multiple agents / "first X then Y" / "have A and B discuss" | **Pipeline** — proceed to Step 2 to produce a plan |
| Ongoing development, needs context retention | **Chat** — `/chat ko` to enter session mode |
| **Estimated >60s** / long task / needs IM push / user says "notify me when done" | **Async job** — `POST /jobs` (see [references/async-jobs.md](references/async-jobs.md)); plan card must mark execution as "async" |

**Duration estimation guide** (when uncertain, round up — err on the high side):

| Task characteristic | Estimate |
|---------------------|----------|
| One-liner Q&A, single-file read/write | <30s |
| Single-agent review of a large file, writing a medium-sized snippet | 30–60s |
| Multi-agent pipeline (any mode) | **>60s, must be async** |
| Conversation mode (2+ agents, multi-turn) | **>60s, must be async** |
| Involves shell execution, grep across many files, running tests | **>60s, must be async** |
| User explicitly says "wait for the result" / "need it now" + trivial task | Keep sync |

### Step 2 — Pick agents (static + on-demand)

Bridge agents come from two sources; **consider both before orchestrating**:

| Source | Characteristics | When to use |
|--------|-----------------|-------------|
| **Static agents** (kiro/claude/codex/qwen/opencode/harness…) | Registered in config, long-lived | General tasks, known strengths, fast response |
| **Harness-factory dynamic agents** | `POST /harness` spawns one from a preset on demand; can be batched | Need a specific permission set, specialized role, or several distinct presets collaborating at once |

Fetch the live list (it may include dynamic harnesses the user created earlier):

```bash
curl -s -H "Authorization: Bearer $ACP_TOKEN" "$ACP_BRIDGE_URL/agents" | jq '.agents[].name'
```

Selection guidance:
- Single general task (review / translate / summarize) → use a static agent directly
- Need fine-grained permissions (read-only file access, restricted shell) → use a harness preset (`reader` / `executor` / `reviewer` …)
- Orchestration with **distinct roles** (review + run + write) → spawn one harness per role, then chain/parallelize
- One-off task, no reuse → clean up with `DELETE /harness/<name>` when done

Batch-spawn example (run this before building the pipeline):

```bash
# Spawn three specialized roles
for preset in reviewer executor writer; do
  curl -sS -X POST "$ACP_BRIDGE_URL/harness" -H "Authorization: Bearer $ACP_TOKEN" \
    -H "Content-Type: application/json" \
    -d "{\"profile\":\"$preset\"}" | jq -r '.name'
done
# Use the returned names in the `agent` field of pipeline steps
```

Rationale: the seven hardcoded aliases above are only samples. Actual available agents = static agents + dynamic harnesses the user spawned previously + harnesses you are about to spawn for this task.

### Step 3 — Present the plan (don't execute yet)

The plan card must expose every **key decision** to the user, not just the steps. Use this fixed format:

```
📋 Execution plan

**Decision summary**
- Execution mode: sync / async (async job pushes result to IM; **>60s must be async**)
- Agent count: single / multiple (N)
- Orchestration mode: sequence | parallel | race | random | conversation | —
- Max turns: N turns (conversation only; other modes leave as —)
- Timeout: N seconds (default 600)
- Push target: Discord channel / Feishu user / — (only needed for async)

**Steps**

| # | Agent | Task | Output var |
|---|-------|------|-----------|
| 1 | kiro | Review src/agents.py for bugs | review |
| 2 | claude | Based on {{review}}, write pytest tests | — |

**Harnesses to create** (omit this section if none)

| Name | Preset | Purpose |
|------|--------|---------|
| reviewer-log42 | reviewer | Read-only code review |

Reply `yes` to execute. Otherwise say what to change (swap agent / change mode / change turns / switch to async…).
```

Rules:
- **All 6 decision-summary lines are mandatory** (write `—` for fields that don't apply)
- **Always** show the plan for pipeline / harness creation / async job / single-agent tasks estimated to exceed 30s
- **Never** show a plan for a `/cli` single call or `/chat` forward — execute immediately
- In the steps table, fill in the `Output var` column with the `output_as` variable name when context is passed forward; otherwise use `—`
- `Max turns` is only populated for `conversation` mode (default 6, up to 12); for other modes use the step count (just the number of rows)
- If the plan needs temporary harnesses, list them in the separate "Harnesses to create" table
- **Sync/async hard constraint**: when estimated duration is >60s, the "Execution mode" field **must** be "async". Pipelines and conversation mode are >60s by default. If the user insists on running a long task synchronously, warn "This is expected to take over a minute; async is recommended, otherwise the client will block" and confirm again.
- The final confirmation keyword is **`yes` only** (case-insensitive; Chinese equivalents "是 / 执行 / 确认 / go" also accepted). Any other reply is treated as revision feedback — go back to Step 3 and regenerate the plan.

### Step 4 — On `yes`, execute and relay the ID

Only `yes` (case-insensitive; also "go / 执行 / 确认 / 是" counts) triggers execution. Any other reply is treated as revision feedback — go back to Step 3 and regenerate the plan.

**Every execution response must echo the ID** (so the user can follow up / track pushes):

| Task type | Required field | Example |
|-----------|---------------|---------|
| Async job | `job_id` | `✅ Async job submitted, job_id: abc123-def4-...` + `GET /jobs/<id>` for status |
| Pipeline | `pipeline_id` | `🔗 Pipeline started, pipeline_id: xyz789-...` + `GET /pipelines/<id>` for status |
| Dynamic harness creation | returned `name` | `🏭 Harness created, agent name: researcher-abc1` |
| Synchronous `/cli` single call | No ID needed | Show the agent output directly |
| Chat | No ID needed (session_id is already in chat-state.json) | — |

The ID in the response **must be the full value** (do not truncate to the first 8 chars); users need it intact to query. If Bridge does not return the expected field, treat it as a failure and surface the error.

### Step 4.1 — On failure, suggest a fallback (don't just report)

When a pipeline / job returns `failed` or a step errors, surface the error verbatim **and** add a one-line fallback suggestion. Do not silently retry — let the user decide.

| Failure signal | Suggest |
|----------------|---------|
| Step `failed: agent timeout (idle 300s)` on codex (PTY) | "Retry with ACP agent (kiro/claude) for this step, or split the prompt into smaller parts" |
| Step `completed` but output empty / contains "User refused permission" | "Bridge permission-reply schema mismatch — check Bridge version ≥ v0.13.3 (see troubleshooting)" |
| Pipeline status `failed`, first step worked, later step couldn't read shared_cwd file | "Previous step likely didn't actually write the file — re-run that step alone in `/cli` to verify, then resume" |
| `pool_exhausted` | "Too many concurrent sessions — wait 30s and retry, or reduce parallelism" |
| Harness spawn 200 but first call errors | "Check `/harness/presets`; preset name may be invalid or harness-factory binary missing (see troubleshooting)" |

If the user says "retry" without new direction, re-run **the same plan** once; if it fails the same way, stop and ask before a third attempt.

### Step 5 — Mode recognition cheatsheet

For 2–5 agents, prefer a preset template from [references/orchestration-patterns.md](references/orchestration-patterns.md) — pick a template name first, then fill in agents and prompts. Only fall back to ad-hoc planning for >5 agents or when no template fits.

| User says | Mode | Typical size |
|-----------|------|--------------|
| "first have X do... then have Y do..." | `sequence` | 2–5 steps |
| "ask X and Y at the same time" / "in parallel" / "multiple perspectives" | `parallel` | 2–4 agents |
| "whoever is fastest" / "race" | `race` | 2–4 agents |
| "just pick one" / "random" | `random` | 2–N candidates |
| "have X and Y discuss..." / "debate" | `conversation` | default 6 turns, max 12 |

### Step 6 — Common intent → plan quick lookup

| User intent | Suggested plan |
|-------------|----------------|
| "review this code" | Single `/cli ko` — no pipeline needed |
| "review then write tests" | sequence: kiro review → claude tests |
| "compare kiro's and claude's approaches" | parallel: kiro + claude, compare outputs manually |
| "let them discuss this design" | conversation, 2 participants, topic = the design in question |
| "build me a weather-query agent" | `POST /harness` with the `operator` preset |
| "have a code reviewer work with a test runner" | Batch-spawn 2 harnesses (`reviewer` + `developer`), then `sequence` them |
| "analyze this log" | Single call to the `analyst` harness or kiro |

### Step 7 — Clarification heuristics

When intent is ambiguous, ask **one** short question, not a list. Prefer defaults over interrogation:

| Missing info | Default action |
|--------------|---------------|
| No agent specified | Ask: "Which one? kiro is strong at coding, claude at review" |
| No collaboration mode (two agents but direction unclear) | Ask: "Sequential relay (former feeds the latter) or parallel + aggregate?" |
| Vague verb ("deal with this" / "handle it") | Ask: "Specifically — review, refactor, or write tests?" |
| Other details (cwd, arguments) | **Do not ask**; use default `/tmp` or let the agent decide |

## Pipeline

When user asks multiple agents to collaborate, see [references/pipeline.md](references/pipeline.md).

## Dynamic Harness

When user asks to create a specialized agent, use `POST /harness` with a preset name.

### Model Selection (harness-factory 0.6.0+)

| Value | Behavior |
|-------|----------|
| `"auto"` or omit | Random model from 8 built-in (claude, deepseek, kimi, qwen, glm, minimax, gemma, opus) |
| alias e.g. `"claude-sonnet"` | Specific model by alias |
| full ID e.g. `"bedrock/..."` | Exact model ID |

Auto mode includes fallback: if a model fails, harness-factory automatically tries the next one.

### Preset → Intent Mapping

| User intent | Preset |
|-------------|--------|
| Read files, look at code | `reader` |
| Run commands, inspect system | `executor` |
| Fetch web pages, search | `scout` |
| Review code, inspect diffs | `reviewer` |
| Analyze data, statistics | `analyst` |
| Research, gather info and summarize | `researcher` |
| Write code, run tests | `developer` |
| Write docs, look up references | `writer` |
| Ops, deploy, network | `operator` |
| Full permissions, do anything | `admin` |

### Usage

```bash
# Create with preset name (harness-factory handles the rest)
curl -X POST "$ACP_BRIDGE_URL/harness" \
  -H "Authorization: Bearer $ACP_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"profile": "operator", "system_prompt": "Help the user build a weather-query skill"}'

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
- [references/orchestration-patterns.md](references/orchestration-patterns.md) — Preset templates for 2–5 agent orchestration
- [references/async-jobs.md](references/async-jobs.md) — Async jobs, target format, callback, monitoring
- [references/troubleshooting.md](references/troubleshooting.md) — Error diagnosis
- [scripts/acp-client.sh](scripts/acp-client.sh) — Bash client used by `/cli` and `/chat`
- [AGENT.md](../AGENT.md) — First-time setup guide
