---
name: acp-bridge-caller
description: "v0.13.0 — 通过 ACP Bridge HTTP API 调用远程 CLI agent，支持自然语言编排拆解 + 多 agent pipeline。Usage: /cli <prompt> | /cli ko <prompt> (kiro) | /cli cc <prompt> (claude) | /chat ko (进入对话模式)"
disable-model-invocation: true
---

# ACP Bridge Caller

Call remote CLI agents via ACP Bridge HTTP API.

```bash
ACP_CLIENT="${CLAUDE_SKILL_DIR}/acp-client.sh"
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
| 单个动词、明确一个 agent、一问一答 | **Single call** — `/cli xx "..."` 直接跑，不需要确认 |
| 多动词 / 多 agent / "先 X 再 Y" / "让 A 和 B 讨论" | **Pipeline** — 走 Step 2 出 plan |
| 持续开发、要记住上下文 | **Chat** — `/chat ko` 进入会话模式 |
| 长任务、要推送到 IM | **Async job** — `POST /jobs`（见 [reference.md](reference.md)） |

### Step 2 — Fetch current agents

Pipeline / dynamic harness 前必须拉一次实时列表，**不要**依赖本文件写死的 7 个别名：

```bash
curl -s -H "Authorization: Bearer $ACP_TOKEN" "$ACP_BRIDGE_URL/agents" | jq '.agents[].name'
```

原因：用户可能通过 `POST /harness` 动态建了专家 agent（如 `researcher-xxx`），写死的清单会错过它们。

### Step 3 — Present the plan (don't execute yet)

Show a markdown table, then wait for user to reply `yes`:

```
📋 计划：

| # | Agent | 任务 | 输出变量 |
|---|-------|------|---------|
| 1 | kiro | Review src/agents.py for bugs | review |
| 2 | claude | Based on {{review}}, write pytest tests | — |

Mode: sequence
回复 `yes` 执行，或说明需要调整。
```

Rules:
- **Always** show plan for pipeline / harness creation / async job
- **Never** show plan for single `/cli` call or `/chat` forwarding — 直接执行
- Plan 表必须包含 agent、任务、若有上下文传递则标注 `output_as` 变量名
- 末尾必须写 `Mode: <sequence|parallel|race|random|conversation>`

### Step 4 — On `yes`, execute

Only `yes`（大小写无关，或直接说 "go / 执行 / 确认" 也算）才执行。其他回复当作修订意见，回到 Step 3 重出 plan。

### Step 5 — Mode recognition cheatsheet

| User says | Mode |
|-----------|------|
| "先让 X 做...再让 Y 做..." | `sequence` |
| "同时问 X 和 Y" / "并行" / "多视角" | `parallel` |
| "谁快用谁" / "竞速" | `race` |
| "随便找一个" / "随机" | `random` |
| "让 X 和 Y 讨论..." / "辩论" | `conversation` |

### Step 6 — Common intent → plan quick lookup

| User intent | Suggested plan |
|-------------|---------------|
| "review 这段代码" | Single `/cli ko` — 无需 pipeline |
| "review 后再写单测" | sequence: kiro review → claude tests |
| "对比 kiro 和 claude 的方案" | parallel: kiro + claude，人工对比输出 |
| "让它们讨论一下这个设计" | conversation, 2 participants, topic = 设计主题 |
| "帮我搞个查天气的 agent" | `POST /harness` with `operator` preset |
| "分析这份日志" | Single call to `analyst` harness 或 kiro |

### Step 7 — Clarification heuristics

When intent is ambiguous, ask **one** short question, not a list. Prefer defaults over interrogation:

| Missing info | Default action |
|--------------|---------------|
| 没指定 agent | 问一句："用哪个？kiro 擅长 coding，claude 擅长 review" |
| 没指定协作方式（两个 agent 但不明方向） | 问："串行接力（前者输出喂后者）还是并行汇总？" |
| 动词模糊（"处理一下" / "搞定这个"） | 问："具体是 review、重构、还是写测试？" |
| 其他细节（cwd、参数） | **不要问**，用默认值 `/tmp` 或让 agent 自己判断 |

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
| 读文件、看代码 | `reader` |
| 跑命令、查系统 | `executor` |
| 查网页、搜资料 | `scout` |
| 审代码、看 diff | `reviewer` |
| 分析数据、统计 | `analyst` |
| 调研、查资料写总结 | `researcher` |
| 写代码、跑测试 | `developer` |
| 写文档、查参考 | `writer` |
| 运维、部署、查网络 | `operator` |
| 全权限、什么都能干 | `admin` |

### Usage

```bash
# Create with preset name (harness-factory handles the rest)
curl -X POST "$ACP_BRIDGE_URL/harness" \
  -H "Authorization: Bearer $ACP_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"profile": "operator", "system_prompt": "帮用户写查天气的 skill"}'

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
- [reference.md](reference.md) — Async jobs, target format, callback, monitoring
- [troubleshooting.md](troubleshooting.md) — Error diagnosis
- [AGENT.md](../AGENT.md) — First-time setup guide
