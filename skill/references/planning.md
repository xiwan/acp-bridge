# Planning Workflow

Classify → pick agents → present plan → execute.

## Step 1 — Classify intent

| Signal | Route |
|--------|-------|
| Single verb, one agent, ≤60s | `/cli xx "..."` directly, no plan |
| Multiple agents / "first X then Y" / "discuss" | Pipeline → Step 2 |
| Ongoing development, needs context | `/chat ko` |
| >60s / long task / "notify me" | Async `POST /jobs` |

Rule: estimated >60s → **must be async**. Multi-agent pipelines and conversations are always >60s.

## Step 2 — Pick agents

Two sources:
- **Static agents**: kiro, claude, codex, qwen, opencode, hermes, harness, opengame
- **Dynamic harness** (`POST /harness`): specialized roles with preset permissions

Key rule: steps that **write files or run shell** → use static agents (kiro/claude) or harness with write-capable preset (developer/operator/admin). Read-only tasks → harness (reviewer/analyst/reader).

Fetch live list: `curl -s -H "Authorization: Bearer $ACP_TOKEN" "$ACP_BRIDGE_URL/agents" | jq '.agents[].name'`

For preset details see [harness-presets.md](harness-presets.md).

## Step 3 — Present the plan

```
📋 Execution plan

**Decision summary**
- Execution mode: sync / async
- Agent count: N
- Orchestration: sequence | parallel | race | conversation | —
- Max turns: N (conversation only; else —)
- Timeout: Ns
- Push target: channel / —

**Steps**

| # | Agent | Task | Output var |
|---|-------|------|-----------|
| 1 | ... | ... | ... |

Reply `yes` to execute.
```

Rules:
- All decision-summary lines mandatory (write `—` when n/a)
- Show plan for pipeline / harness creation / async job / tasks >30s
- Never show plan for single `/cli` call — execute immediately
- For 2–5 agents, pick a template from [orchestration-patterns.md](orchestration-patterns.md)
- Confirmation keyword: `yes` (also `go / execute / confirm`)

## Step 4 — Execute and relay ID

| Task | Echo |
|------|------|
| Async job | `job_id` |
| Pipeline | `pipeline_id` |
| Dynamic harness | `name`, `preset`, `model` |
| Sync `/cli` | Agent output directly |

For pipeline completion, append duration: `⏱️ kiro 5.3s · claude 8.5s · total 31.9s`

## Step 4.1 — On failure

Surface error verbatim + one line guidance. See [troubleshooting.md](troubleshooting.md).

- `agent timeout` on PTY → swap to ACP agent
- `[loop detected]` → preset can't write; use static agent
- Instant completion with raw XML → model incompatible; use `deepseek-v3` or `claude-sonnet`
- Identical failure twice → stop and ask

## Step 5 — Mode selection

```
Does B need A's output?
 YES → sequence (or conversation for dialog)
 NO  → parallel / race
```

| User says | Mode |
|-----------|------|
| "first X, then Y" | `sequence` |
| "same time" / "parallel" | `parallel` |
| "whoever is fastest" | `race` |
| "discuss" / "debate" | `conversation` (default 6 turns) |

## Step 6 — Quick lookup

| Intent | Plan |
|--------|------|
| "review this code" | Single `/cli ko` or `/cli cc` |
| "review then write tests" | sequence: reviewer → claude |
| "compare answers" | parallel: kiro + claude |
| "have X and Y discuss" | conversation, 2 participants |
| "build an agent" | `POST /harness` |

## Step 7 — Clarification

Ask **one** short question, not a list. Prefer defaults over interrogation.

| Missing | Action |
|---------|--------|
| No agent specified | "Which one? kiro for coding, claude for review" |
| Collaboration unclear | "Sequential or parallel?" |
| Vague verb | "Review, refactor, or write tests?" |
| Other details | Don't ask — use defaults |
