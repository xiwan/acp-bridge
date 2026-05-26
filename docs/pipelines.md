[← API Reference](api-reference.md) | [Async Jobs →](async-jobs.md)

> **Docs:** [Getting Started](getting-started.md) · [Tutorial](tutorial.md) · [Configuration](configuration.md) · [Agents](agents.md) · [API Reference](api-reference.md) · [Pipelines](pipelines.md) · [Async Jobs](async-jobs.md) · [Webhooks](webhooks.md) · [Client Usage](client-usage.md) · [Tools Proxy](tools-proxy.md) · [Security](security.md) · [Process Pool](process-pool.md) · [Testing](testing.md) · [Troubleshooting](troubleshooting.md)

# Orchestration — Multi-Agent Pipelines

## Which mode should I use?

| I want to... | Mode | Example |
|--------------|------|---------|
| Chain agents — output of A feeds into B | **sequence** | Code review → test generation → QA |
| Compare answers from multiple agents | **parallel** | Kiro vs Claude on the same question |
| Get the fastest answer, cancel the rest | **race** | Latency-sensitive one-shot tasks |
| Have agents discuss and build on each other | **conversation** | Architecture debate, collaborative design |

## Modes

- Sequence: chain agents, each receives the previous output as context
- Parallel: fan-out the same prompt to multiple agents, aggregate results
- Race: fastest agent wins, others are cancelled
- Conversation: agents take turns in a multi-round discussion
- Harness Factory support: profile-driven lightweight agents via [harness-factory](https://github.com/xiwan/harness-factory) — same binary, different profiles, different agents

## Composable Pipelines (v0.19.0)

Pipelines can be **composed** across multiple API calls by sharing a workspace directory. This enables multi-phase workflows without hardcoding the flow.

### Workspace Inheritance

Pass `shared_cwd` in `context` to reuse a previous pipeline's workspace:

```bash
# Phase 1: agents discuss game design
curl -X POST http://localhost:18010/pipelines \
  -H "Authorization: Bearer $TOKEN" \
  -d '{
    "mode": "conversation",
    "participants": ["kiro", "claude", "harness"],
    "topic": "设计一个贪吃蛇游戏，讨论架构和分工",
    "config": {"max_turns": 8, "stop_conditions": ["DONE"], "output_schema": true}
  }'
# Returns: {"pipeline_id": "abc-123", "shared_cwd": "/tmp/acp-pipelines/conversation/conv-abc12345"}

# Phase 2: parallel implementation — inherits the same workspace
curl -X POST http://localhost:18010/pipelines \
  -H "Authorization: Bearer $TOKEN" \
  -d '{
    "mode": "parallel",
    "context": {"shared_cwd": "/tmp/acp-pipelines/conversation/conv-abc12345"},
    "steps": [
      {"agent": "kiro", "prompt": "Implement the frontend based on the design in this workspace"},
      {"agent": "claude", "prompt": "Implement the backend based on the design in this workspace"}
    ]
  }'
```

### Structured Output Extraction

Set `output_schema` in conversation config to extract JSON from the final agent turn:

```json
"config": {"output_schema": true, "stop_conditions": ["DONE"]}
```

The agent's last turn should contain a JSON block (in \`\`\`json fences or inline). Bridge extracts it into `pipeline.context.output`, accessible via `GET /pipelines/{id}`.

### Auto-Chain (`next`)

Define `next` in context to auto-trigger the next pipeline on completion — no manual API call needed:

```json
{
  "mode": "conversation",
  "participants": ["kiro", "claude"],
  "topic": "设计贪吃蛇游戏并分工",
  "config": {"max_turns": 8, "stop_conditions": ["DONE"], "output_schema": true},
  "context": {
    "next": {
      "mode": "parallel",
      "steps_from_output": true,
      "step_prompt_template": "在 {shared_cwd} 中实现 {module}，负责文件: {files}"
    }
  }
}
```

- `next.mode` — next pipeline mode (sequence/parallel/race/conversation)
- `next.steps` — static steps (if known ahead of time)
- `next.steps_from_output` — dynamically generate steps from `output.tasks[]`
- `next.step_prompt_template` — template for each generated step (vars: `{shared_cwd}`, `{module}`, `{files}`, `{agent}`)

The next pipeline automatically inherits `shared_cwd` and `output`. Chain result is stored in `next_pipeline_id`.

### Human-in-the-Loop

#### Pause / Resume

```bash
POST /pipelines/{id}/pause     # Pause before next turn
POST /pipelines/{id}/resume    # Resume execution
```

#### Inject Message

```bash
POST /pipelines/{id}/inject
{"message": "用 Phaser.js 框架，不要用 Canvas 原生 API"}
```

Injected messages appear as `[Human]` turns in the transcript. The next agent responds to the injected message. If the pipeline is paused, inject auto-resumes it.

### Artifacts

List files created by agents in the shared workspace:

```bash
GET /pipelines/{id}/artifacts
# Returns: {"shared_cwd": "...", "files": [{"path": "game.js", "size": 1234}, ...]}
```

## Live Observability (v0.21.0)

Three ways to follow a running pipeline in real time, choose by use case:

### 1. SSE event stream — recommended for programmatic clients

Subscribe to lifecycle events as they happen. Stream auto-closes on `pipeline_done`.

```bash
curl -sN http://localhost:18010/pipelines/{id}/events \
  -H "Authorization: Bearer $ACP_BRIDGE_TOKEN"
```

Event types:

| event | data |
|-------|------|
| `pipeline_started` | `{pipeline_id, mode, steps, shared_cwd, agents}` |
| `step_started` | `{index, agent, prompt_preview}` |
| `step_completed` | `{index, agent, duration, status, result_preview}` |
| `step_failed` | `{index, agent, duration, status, error}` |
| `pipeline_done` | `{pipeline_id, status, duration, error}` |
| `heartbeat` | `{ts}` (every 15s during running steps) |

**Late-connect replay**: connecting to a pipeline that already started (or finished) replays the full event history first, then streams live (or closes if already done). This means clients don't need to subscribe before submitting.

**Quick CLI**: `tools/pipeline-events.sh <pid>` wraps the SSE stream with human-readable output.

```
[06:29:10] 🚀 pipeline_started  mode=sequence steps=3  harness → opengame → kiro
[06:29:11] ▶️  step_started     idx=0 agent=harness
[06:29:18] ✅ step_completed   idx=0 agent=harness dur=6.8s  | <preview>
[06:29:19] ▶️  step_started     idx=1 agent=opengame
[...]
```

### 2. Polling — for shell scripts

```bash
GET /pipelines/{id}                       # full status snapshot
GET /pipelines/{id}/steps/{idx}/live      # streaming buffer of a running step
```

CLI helper: `tools/pipeline-watch.sh <pid> [interval]` polls and prints transitions only.

### 3. Webhook push — for IM integration

Add `target` and `channel` to the pipeline submission:

```json
{
  "mode": "sequence",
  "target": "channel:1469723146134356173",
  "channel": "discord",
  "steps": [...]
}
```

Each step completion immediately posts a formatted message to your IM channel via the configured `webhook.url`. See [webhooks.md](webhooks.md) for the payload format.

---

## Architecture — Interaction Modes

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                    ACP Bridge Multi-Agent Interaction Modes                     │
└─────────────────────────────────────────────────────────────────────────────────┘

                            ┌─────────────────┐
                            │   User          │
                            │   (Discord/IM)  │
                            └────────┬────────┘
                                     │
    ┌────────────────┬───────────────┼───────────────┬────────────────┐
    │                │               │               │                │
    ▼                ▼               ▼               ▼                ▼
┌────────┐    ┌──────────┐    ┌──────────┐    ┌──────────┐    ┌──────────────┐
│  /cli  │    │  /chat   │    │  /jobs   │    │/pipeline │    │   /run       │
│  ────  │    │  ─────   │    │  ─────   │    │ ──────── │    │   ────       │
│Single  │    │ Session  │    │ Async    │    │ Multi-   │    │ (OpenClaw    │
│ shot   │    │  Mode    │    │ Task     │    │ Agent    │    │  native)     │
└────┬───┘    └────┬─────┘    └────┬─────┘    └────┬─────┘    └──────┬───────┘
     │             │               │               │                 │
     └─────────────┴───────────────┴───────────────┴─────────────────┘
                                   │
                                   ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│                              ACP Bridge                                         │
│  ┌─────────┐ ┌─────────┐ ┌─────────┐ ┌─────────┐ ┌──────────┐                  │
│  │  kiro   │ │ claude  │ │  codex  │ │  qwen   │ │ opencode │                  │
│  └─────────┘ └─────────┘ └─────────┘ └─────────┘ └──────────┘                  │
└─────────────────────────────────────────────────────────────────────────────────┘

═══════════════════════════════════════════════════════════════════════════════════
                               5 Interaction Modes
═══════════════════════════════════════════════════════════════════════════════════

1. /cli — Single-shot (同步单次)
   /cli ko "task"  →  agent 执行  →  结果返回
   特点：一问一答，无状态

2. /chat — Session Mode (对话模式)
   /chat ko  →  进入对话  →  后续消息自动透传  →  /chat end 退出
   特点：保持上下文，适合多轮开发

3. /jobs — Async (异步后台)
   POST /jobs { agent, prompt, target }  →  立即返回  →  完成后自动推送
   特点：非阻塞，适合长任务

4. /pipeline — Multi-Agent Orchestration (多 Agent 编排)

   mode: "sequence" — 串行接力
   ┌──────────────────────────────────────────────────────────────┐
   │  kiro ──► claude ──► codex ──► qwen ──► opencode            │
   │    │        │          │         │           │              │
   │  输出A    输出B      输出C     输出D       输出E             │
   │    └────────┴──────────┴─────────┴───────────┘              │
   │           前一个的输出作为后一个的上下文                     │
   └──────────────────────────────────────────────────────────────┘

   mode: "parallel" — 并行执行
   ┌──────────────────────────────────────────────────────────────┐
   │          ┌──► kiro ────► 输出A ──┐                          │
   │          │                       │                          │
   │  prompt ─┼──► claude ──► 输出B ──┼──► 汇总所有结果          │
   │          │                       │                          │
   │          ├──► codex ───► 输出C ──┤                          │
   │          │                       │                          │
   │          └──► qwen ────► 输出D ──┘                          │
   └──────────────────────────────────────────────────────────────┘

   mode: "race" — 竞速，最快者胜
   ┌──────────────────────────────────────────────────────────────┐
   │          ┌──► kiro ────► ✗ (running)                        │
   │  prompt ─┼──► claude ──► ✗ (running)                        │
   │          ├──► codex ───► ✓ WINNER! → 返回，取消其他         │
   │          └──► qwen ────► ✗ (running)                        │
   └──────────────────────────────────────────────────────────────┘

5. /run (sessions_spawn) — OpenClaw Native Integration
   sessions_spawn(runtime="acp", agentId="kiro", task="...")
   特点：OpenClaw 原生 API，支持 thread 绑定、streaming

═══════════════════════════════════════════════════════════════════════════════════
                                  Summary
═══════════════════════════════════════════════════════════════════════════════════

  Mode      │ Blocking │ Context │ Multi-Agent │ Use Case
 ───────────┼──────────┼─────────┼─────────────┼────────────────────────────────
  /cli      │ Yes      │ No      │ No          │ Quick one-off tasks
  /chat     │ Yes      │ Yes     │ No          │ Multi-turn development
  /jobs     │ No       │ No      │ No          │ Long-running background work
  /pipeline │ Varies   │ Varies  │ Yes ✓       │ Agent orchestration
    sequence│ Yes      │ Chained │             │   串行接力
    parallel│ Yes      │ No      │             │   并行 / 多视角
    race    │ Yes      │ No      │             │   竞速取优
    convers.│ Yes      │ Per-agent│            │   多轮对话协作
  /run      │ No       │ Yes     │ No          │ OpenClaw native spawn
```
