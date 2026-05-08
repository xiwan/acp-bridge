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

## Composable Multi-Phase Template: `discuss-then-build`

> 适用场景：多 agent 协作开发（游戏、应用、项目），先讨论设计再分头实现。

### 流程概览

```
conversation (讨论+分工) ──► parallel (分头实现) ──► sequence (集成+review)
         │                        │                        │
    共享 shared_cwd ─────────────────────────────────────────
```

### Phase 1: Conversation — 讨论设计 & 角色分工

一次 API 调用，conversation 结束后自动触发 parallel 实现：

```json
{
  "mode": "conversation",
  "participants": ["kiro", "claude", "harness"],
  "topic": "设计一个贪吃蛇游戏。讨论技术架构、模块划分，并分配每人负责的模块。最终以 JSON 输出分工方案，格式：{\"tasks\":[{\"agent\":\"...\",\"module\":\"...\",\"files\":[\"...\"]}]}。讨论结束时输出 STATUS: CONSENSUS",
  "config": {
    "max_turns": 8,
    "stop_conditions": ["DONE", "CONSENSUS"],
    "output_schema": true,
    "a2a_rules": true
  },
  "context": {
    "next": {
      "mode": "parallel",
      "steps_from_output": true,
      "step_prompt_template": "在 {shared_cwd} 中实现 {module}，负责文件: {files}。完成后执行 wc -c 确认文件非空。"
    }
  }
}
```

**自动流程**：conversation 达成共识 → 提取 output JSON → 生成 parallel steps → 自动执行

**人类介入点**（可选，不影响自动流程）：
- `POST /pipelines/{id}/pause` — 暂停观察讨论方向
- `POST /pipelines/{id}/inject {"message": "用 Phaser.js，不要原生 Canvas"}` — 注入决策

### Phase 2（手动模式）: Parallel — 分头实现

如果不用 `next` 自动链，也可以手动触发：

```json
{
  "mode": "parallel",
  "context": {"shared_cwd": "<Phase 1 返回的 shared_cwd>"},
  "steps": [
    {"agent": "kiro", "prompt": "在 {{shared_cwd}} 中实现前端游戏界面"},
    {"agent": "claude", "prompt": "在 {{shared_cwd}} 中实现游戏逻辑"}
  ]
}
```

### Phase 3 (可选): Sequence — 集成 & Review

```json
{
  "mode": "sequence",
  "context": {"shared_cwd": "<同一个 shared_cwd>"},
  "steps": [
    {"agent": "kiro", "prompt": "集成 {{shared_cwd}} 中的前后端代码，确保可运行"},
    {"agent": "harness", "prompt": "review {{shared_cwd}} 中所有代码，输出质量报告"}
  ]
}
```

### 查看产出

```bash
GET /pipelines/{id}/artifacts  # 列出 workspace 中的文件
```

### 变体

| 变体 | 区别 |
|------|------|
| 全自动 | 不 pause，三个 phase 脚本串起来 |
| 人类拍板 | Phase 1 结束后人工确认分工再启动 Phase 2 |
| 跳过讨论 | 直接 parallel，prompt 里写死分工 |
| 单 phase | 只用 conversation，agents 边讨论边在 shared_cwd 写代码 |
| 竞速选方案 | Phase 1 用 race 让多 agent 各出方案，人选最优再进 Phase 2 |
