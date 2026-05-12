# Orchestration Patterns — 2 to 5 Agents

Preset templates. Pick a shape, fill in agents and prompts.

Notation: `A → B` sequence · `A ‖ B` parallel · `A ↔ B` conversation

## 2 Agents

| Template | Shape | Mode | When |
|----------|-------|------|------|
| `relay` | `A → B:refine({{out}})` | sequence | "first X, then Y builds on it" |
| `dual-view` | `A ‖ B` | parallel | "compare two perspectives" |
| `debate-2` | `A ↔ B` | conversation (6 turns) | "discuss / debate" |
| `race-2` | `A ‖ B` (same prompt) | race | "whoever is fastest" |

## 3 Agents

| Template | Shape | Mode | When |
|----------|-------|------|------|
| `review-write-test` | `A → B → C` | sequence | review → implement → test |
| `fan-out-merge` | `A ‖ B ‖ C` | parallel | "three perspectives" |
| `roundtable-3` | `A ↔ B ↔ C` | conversation (9 turns) | "three discuss a design" |

## 4–5 Agents

| Template | Shape | Mode | When |
|----------|-------|------|------|
| `staged-pipeline` | `A → B → C → D` | sequence | multi-stage workflow |
| `parallel-then-judge` | `(A ‖ B ‖ C) → D` | parallel + sequence | "candidates + adjudicator" |
| `dual-debate-then-judge` | `(A ↔ B) → C` | conversation + sequence | "two debate, third rules" |

## Picking a Template

| User phrasing | Template |
|---------------|----------|
| "first X then Y" | `relay` or `staged-pipeline` |
| "ask both / compare" | `dual-view` or `fan-out-merge` |
| "discuss / debate" | `debate-2` / `roundtable-3` |
| "whoever is fastest" | `race-2` |
| "review + implement + test" | `review-write-test` |
| "candidates + judge" | `parallel-then-judge` |

## Multi-Phase Template: `discuss-then-build`

Use case: collaborative development — discuss design first, then implement in parallel.

```
conversation (discuss) → parallel (implement) → sequence (integrate + review)
         └── all share same shared_cwd ──┘
```

### Phase 1: Conversation

```json
{
  "mode": "conversation",
  "participants": ["kiro", "claude", "harness"],
  "topic": "Design a snake game. Discuss architecture and assign modules. Output JSON: {\"tasks\":[{\"agent\":\"...\",\"module\":\"...\",\"files\":[]}]}. Output STATUS: CONSENSUS when done",
  "config": {"max_turns": 8, "stop_conditions": ["DONE", "CONSENSUS"], "output_schema": true},
  "context": {
    "next": {"mode": "parallel", "steps_from_output": true,
             "step_prompt_template": "Implement {module} in {shared_cwd}, files: {files}. Run wc -c to confirm."}
  }
}
```

Auto-chain: consensus → extract JSON → generate parallel steps → execute.

Human intervention: `POST /pipelines/{id}/pause` or `/inject {"message": "Use Phaser.js"}`.

### Phase 2: Parallel (manual alternative)

```json
{"mode": "parallel", "context": {"shared_cwd": "<phase1 cwd>"}, "steps": [...]}
```

### Phase 3: Integration & Review (optional)

```json
{"mode": "sequence", "context": {"shared_cwd": "<same>"}, "steps": [
  {"agent": "kiro", "prompt": "Integrate code in {{shared_cwd}}"},
  {"agent": "harness", "prompt": "Review all code, output report"}
]}
```

### Variants

| Variant | Difference |
|---------|-----------|
| Fully automatic | No pause, phases scripted |
| Human approval | Confirm assignment before Phase 2 |
| Skip discussion | Direct parallel with hardcoded assignment |
| Race for proposals | Phase 1 race, human picks, then Phase 2 |
