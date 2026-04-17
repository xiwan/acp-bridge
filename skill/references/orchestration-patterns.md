# Orchestration Patterns — 2 to 5 Agents

Preset templates so the host LLM can pick a shape first, then fill in agents
and prompts — instead of reinventing a plan each time.

Notation:

- `A → B` — step B runs after A, receives A's output (`sequence`)
- `A ‖ B` — A and B run concurrently (`parallel` unless stated otherwise)
- `A ↔ B` — multi-turn dialog (`conversation`)
- `{{var}}` — output variable from a prior step

For more than 5 agents, compose two of these or fall back to ad-hoc planning.

## 2 Agents

| Template | Dep. | Shape | Mode | Default config | When |
|----------|------|-------|------|----------------|------|
| `relay` | strict | `A:task → B:refine({{out}})` | `sequence` | `output_as: out` | "first X, then Y builds on it" (review→test, draft→polish) |
| `dual-view` | independent | `A:task ‖ B:task` | `parallel` | merge client-side | "ask two for different perspectives / compare" |
| `debate-2` | shared | `A ↔ B on topic` | `conversation` | `max_turns: 6`, `stop: [DONE, CONSENSUS]` | "have A and B discuss / debate this" |
| `race-2` | independent | `A ‖ B` (same prompt) | `race` | — | "whoever is fastest" (usually when latency matters) |

## 3 Agents

| Template | Dep. | Shape | Mode | Default config | When |
|----------|------|-------|------|----------------|------|
| `review-write-test` | strict | `A:review → B:write({{review}}) → C:test({{write}})` | `sequence` | two `output_as` chains | code review → implementation → tests (classic dev loop) |
| `fan-out-merge` | independent | `A ‖ B ‖ C` (same prompt) | `parallel` | merge in reply | "three perspectives on the same question" |
| `roundtable-3` | shared | `A ↔ B ↔ C on topic` | `conversation` | `max_turns: 9`, `stop: [DONE, CONSENSUS]` | "let three discuss a design / pick an approach" |

## 4–5 Agents

| Template | Dep. | Shape | Mode | Default config | When |
|----------|------|-------|------|----------------|------|
| `staged-pipeline` | strict | `A → B → C → D` (up to 5) | `sequence` | each stage `output_as` feeds next | multi-stage workflow: research → design → implement → review → doc |
| `parallel-then-judge` | mixed | `(A ‖ B ‖ C) → D:judge({{a}},{{b}},{{c}})` | **two pipelines** (parallel first, then one sequence step) | kick parallel, await all, feed judge | "three candidates, one synthesizer / adjudicator" |
| `dual-debate-then-judge` | mixed | `(A ↔ B) → C:judge(transcript)` | conversation then sequence | conversation first, its full transcript into the judge's prompt | "two debate, a third rules" — use for disagreement resolution |

**Dep. legend**: `strict` = B needs A's output verbatim · `independent` = no data flow between siblings · `shared` = agents read common workspace / each other's turns but don't strictly chain · `mixed` = different phases have different dependency kinds.

## Picking a Template

| User phrasing | Template |
|---------------|----------|
| "first X then Y" / "X then Y" | `relay` (2) or `staged-pipeline` (3+) |
| "ask both / in parallel / compare" | `dual-view` (2) or `fan-out-merge` (3) |
| "discuss / debate / argue it out" | `debate-2` / `roundtable-3` |
| "whoever is fastest / race" | `race-2` |
| "review + implement + test" | `review-write-test` |
| "three candidates, one judge" / "let X adjudicate" | `parallel-then-judge` or `dual-debate-then-judge` |
| More than 5 agents | No preset — compose two templates or write an ad-hoc plan |

## Using a Template

1. Pick the template name above.
2. In the Step 3 plan card, state the template up front: `Template: relay`.
3. Fill in the agents + prompts in the standard Steps table.
4. Keep default config unless the user overrides (turns / timeout / stop).

Concrete API payload shapes live in [pipeline.md](pipeline.md).
