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
