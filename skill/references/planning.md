# Planning Workflow

For natural-language tasks, pick **single call** / **pipeline** / **async job** / **chat**, then confirm before heavy execution.

## Step 1 — Classify intent

| Signal | Route |
|--------|-------|
| Single verb, one agent, Q&A, **≤60s** | **Single call** — `/cli xx "..."` directly, no confirm |
| Multiple verbs/agents, "first X then Y", "A and B discuss" | **Pipeline** — go to Step 2 |
| Ongoing development, needs context | **Chat** — `/chat ko` |
| **>60s** / long task / IM push / "notify me when done" | **Async job** — `POST /jobs` (see [async-jobs.md](async-jobs.md)) |

**Duration estimation** (round up; prefer higher):

| Task shape | Estimate |
|------------|----------|
| One-liner Q&A, single-file read/write | <30s |
| Single-agent review of a large file, medium code snippet | 30–60s |
| Multi-agent pipeline (any mode) | **>60s, async** |
| Conversation mode (2+ agents) | **>60s, async** |
| Shell exec, grep many files, run tests | **>60s, async** |
| User says "wait / need it now" + trivial task | Keep sync |

## Step 2 — Pick agents (static + on-demand)

Two sources — consider **both** before orchestrating:

| Source | When to use |
|--------|-------------|
| Static agents (kiro/claude/codex/qwen/opencode/hermes/harness…) | General tasks, known strengths, fast |
| Harness-factory dynamic agents (`POST /harness`) | Specific permission set, specialized role, or multiple distinct presets collaborating |

Fetch the live list:

```bash
curl -s -H "Authorization: Bearer $ACP_TOKEN" "$ACP_BRIDGE_URL/agents" | jq '.agents[].name'
```

For harness capability & model binding, see [harness-presets.md](harness-presets.md).

**Capability–task gate (run before proceeding to Step 3):**

For every step that involves persisting artifacts, verify the chosen preset has `Write? = yes`. If not:
- Swap to a write-capable preset (`developer` / `writer` / `operator` / `admin`) or static `claude`, or
- Rewrite the task to "return the content in your reply; no file writes"

Task verbs that imply persistence: *write · save · produce a …doc · create …md · output …file · generate a PRD / report / code file*. If any such verb exists on a no-write preset → **STOP and revise**.

**Agent selection rule for pipelines:**

| Task type | Best agent | Why |
|-----------|-----------|-----|
| Write files, run commands, generate code | static `kiro` / `claude` | Reliable tool use, no sandbox restriction |
| Read / review / analyze within shared_cwd | harness (`reviewer` / `analyst` / `reader`) | Sandboxed, read-only, cost-effective |
| QA / verify artifacts | harness (read-only) or static agent | Depends on whether source is inside shared_cwd |

**Typical pipeline pattern**: static agent produces artifacts → harness agent reviews them.

Harness agents are sandboxed to `shared_cwd` and model compatibility varies. For any step that **must** write to disk or run shell commands, **always prefer static agents**. See [pipeline.md](pipeline.md) § Best Practices.

## Step 3 — Present the plan (don't execute yet)

```
📋 Execution plan

**Decision summary**
- Execution mode: sync / async (async = IM push; **>60s must be async**)
- Agent count: single / multiple (N)
- Task dependency: sequential / independent / shared
- Orchestration mode: sequence | parallel | race | conversation | —
- Max turns: N (conversation only; else —)
- Timeout: N seconds (default 600)
- Push target: Discord channel / Feishu user / — (async only)

**Steps**

| # | Agent | Task | Output var |
|---|-------|------|-----------|
| 1 | kiro | Review src/agents.py | review |
| 2 | claude | Based on {{review}}, write pytest tests | — |

Reply `yes` to execute.
```

Rules:
- **All 7 decision-summary lines are mandatory** — write `—` when n/a
- **Always** show plan for pipeline / harness creation / async job / single-agent tasks estimated >30s
- **Never** show plan for a `/cli` single call or `/chat` forward — execute immediately
- If orchestration needs 2–5 agents, **pick a preset template** from [orchestration-patterns.md](orchestration-patterns.md) first
- **Sync/async hard rule**: estimated >60s → mode field is `async`
- Confirmation keyword: **`yes` only** (also `go / 执行 / 确认 / 是`)

## Step 4 — On `yes`, execute and relay the ID

Every execution response must echo the **full** ID (do not truncate):

| Task | Required fields |
|------|----------------|
| Async job | `job_id` |
| Pipeline | `pipeline_id` |
| Dynamic harness creation | returned `name`, `preset`, `model` |
| Sync `/cli` | Show agent output directly |

For **pipeline / conversation completion**, append a **duration breakdown**:

```
⏱️  kiro 5.3s · claude 8.5s · total 31.9s   (6 turns, stop: MAX_TURNS)
```

## Step 4.1 — On failure, suggest a fallback

Surface the error verbatim **and** add one line of guidance. See [troubleshooting.md](troubleshooting.md) for the full diagnosis table.

Key rules:
- `agent timeout` on PTY → swap to ACP agent (kiro/claude)
- `[loop detected]` on harness → preset can't write; use static agent or rewrite prompt
- Harness completes instantly with raw XML/markdown tool calls → model incompatible; specify `deepseek-v3` or `claude-sonnet`
- "Retry" with no new direction → re-run **once**; if identical failure, stop and ask

## Step 5 — Mode cheatsheet

**Judge dependency first** (mode follows from it):

```
Does task B need task A's output?
 ├─ YES (strict chain)            → sequence  (or conversation if multi-turn dialog)
 ├─ NO (independent, can diverge) → parallel / race
 └─ PARTIAL (share workspace)     → conversation  (or parallel + aggregate)
```

Anti-pattern: if tasks are **independent**, do NOT pick `sequence` just because the user said "first X, then Y" — confirm "are B's inputs from A, or just shared context?"

| User says | Mode |
|-----------|------|
| "first X, then Y" | `sequence` |
| "same time" / "parallel" | `parallel` |
| "whoever is fastest" | `race` |
| "discuss" / "debate" | `conversation` (default 6, max 12 turns) |

## Step 6 — Common intent → plan quick lookup

| Intent | Suggested plan |
|--------|----------------|
| "review this code" | Single `/cli ko` or `/cli cc` |
| "review then write tests" | sequence: harness(`reviewer`) → claude writes tests |
| "compare kiro's and claude's" | parallel: kiro + claude |
| "have X and Y discuss" | conversation, 2 participants |
| "build a weather-query agent" | `POST /harness` with `operator` preset |
| "write PRD then implement" | sequence: claude writes PRD → claude implements |

## Step 7 — Clarification heuristics

Ask **one** short question, not a list. Prefer defaults over interrogation:

| Missing | Action |
|---------|--------|
| No agent | "Which one? kiro for coding, claude for review" |
| Collaboration unclear | "Sequential relay or parallel+aggregate?" |
| Vague verb | "Review, refactor, or write tests?" |
| Other details | **Don't ask** — use defaults |
