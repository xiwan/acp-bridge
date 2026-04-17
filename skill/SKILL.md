---
name: acp-bridge-caller
description: "v0.13.3 — 通过 ACP Bridge HTTP API 调用远程 CLI agent，支持自然语言编排拆解（静态 agent + harness 动态生产） + 多 agent pipeline。Usage: /cli <prompt> | /cli ko <prompt> (kiro) | /cli cc <prompt> (claude) | /chat ko (进入对话模式)"
disable-model-invocation: true
---

# ACP Bridge Caller

Call remote CLI agents via ACP Bridge HTTP API.

```bash
ACP_CLIENT="${CLAUDE_SKILL_DIR}/scripts/acp-client.sh"
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
| 单个动词、明确一个 agent、一问一答、**预估 ≤60s** | **Single call** — `/cli xx "..."` 直接跑，不需要确认 |
| 多动词 / 多 agent / "先 X 再 Y" / "让 A 和 B 讨论" | **Pipeline** — 走 Step 2 出 plan |
| 持续开发、要记住上下文 | **Chat** — `/chat ko` 进入会话模式 |
| **预估 >60s** / 长任务 / 要推送到 IM / 用户说"跑完通知我" | **Async job** — `POST /jobs`（见 [references/async-jobs.md](references/async-jobs.md)），plan 卡片标注"执行方式：异步" |

**时长预估指引**（估不准时向上取整，就高不就低）：

| 任务特征 | 预估 |
|---------|------|
| 一句话问答、单文件读/写 | <30s |
| 单 agent review 大文件、写一段中等代码 | 30–60s |
| 多 agent pipeline（任何模式） | **>60s，强制异步** |
| conversation 模式（2+ agent 多轮） | **>60s，强制异步** |
| 涉及 shell 执行、grep 大量文件、跑测试 | **>60s，强制异步** |
| 用户明确说"等结果"/"马上要" + 简单任务 | 保持同步 |

### Step 2 — Pick agents (static + on-demand)

Bridge 的 agent 来自两个来源，**编排前都要考虑**：

| 来源 | 特点 | 何时用 |
|------|------|--------|
| **静态 agent**（kiro/claude/codex/qwen/opencode/harness…） | 配置文件注册，长期常驻 | 通用任务、已知擅长领域、快速响应 |
| **Harness factory 动态 agent** | `POST /harness` 按 preset 即时生成，可批量 | 需要特定权限集、专业角色、或同时要多个不同 preset 协作时 |

拉实时列表（可能包含用户之前建的动态 harness）：

```bash
curl -s -H "Authorization: Bearer $ACP_TOKEN" "$ACP_BRIDGE_URL/agents" | jq '.agents[].name'
```

选型指引：
- 单一通用任务（review / 翻译 / 总结） → 直接用静态 agent
- 需要精细权限（只读文件、只跑特定命令） → 用 harness preset（`reader`/`executor`/`reviewer`…）
- 编排中**多角色分工**（审 + 跑 + 写） → 为每个角色造一个 harness，再串/并
- 一次性任务、不需要复用 → 用完 `DELETE /harness/<name>` 清理

批量造 harness 示例（pipeline 前一步）：

```bash
# 造三个角色
for preset in reviewer executor writer; do
  curl -sS -X POST "$ACP_BRIDGE_URL/harness" -H "Authorization: Bearer $ACP_TOKEN" \
    -H "Content-Type: application/json" \
    -d "{\"profile\":\"$preset\"}" | jq -r '.name'
done
# 返回的 name 填入 pipeline steps 的 agent 字段
```

原因：写死的 7 个别名只是示例。实际可用 = 静态 agent + 用户之前建的动态 harness + 你即将为本次任务新建的 harness。

### Step 3 — Present the plan (don't execute yet)

Plan 卡片必须让用户看懂所有**关键决策**，不只是步骤。固定格式如下：

```
📋 执行计划

**决策摘要**
- 执行方式：同步 / 异步（async job 推送到 IM；**>60s 必须异步**）
- Agent 数：单 / 多（N 个）
- 编排模式：sequence | parallel | race | random | conversation | —
- 最大轮数：N 轮（conversation 专用，其他留 —）
- 超时上限：N 秒（默认 600）
- 推送目标：Discord channel / Feishu user / —（异步任务才需要）

**步骤**

| # | Agent | 任务 | 输出变量 |
|---|-------|------|---------|
| 1 | kiro | Review src/agents.py for bugs | review |
| 2 | claude | Based on {{review}}, write pytest tests | — |

**需要新建的 Harness**（如无则省略本节）

| Name | Preset | 用途 |
|------|--------|------|
| reviewer-log42 | reviewer | 只读审代码 |

回复 `yes` 执行。否则说明要改哪里（换 agent / 改模式 / 改轮数 / 转异步…）。
```

规则：
- **决策摘要 6 行必填**（不适用的字段写 `—`）
- **Always** show plan for pipeline / harness creation / async job / 超过 30s 的单 agent 任务
- **Never** show plan for `/cli` 单 call 或 `/chat` 转发 — 直接执行
- Plan 表 `输出变量` 列若有上下文传递则写 `output_as` 变量名，否则 `—`
- `最大轮数` 仅 `conversation` 模式填（建议默认 6，最多 12）；其他模式用"步数"代替（就是表格行数）
- 若计划要临时造 harness，必须单独列"需要新建的 Harness"表
- **同步/异步强约束**：预估耗时 >60s 时，"执行方式"字段**只能**写"异步"。pipeline 和
  conversation 默认就是 >60s；如果用户强行要求同步长任务，提示"预计超过 1 分钟，建议走
  异步，否则客户端会等待阻塞"并再次确认。
- 末尾确认关键词**只认 `yes`**（大小写无关，中文"是 / 执行 / 确认 / go"同样接受）；其他回复当作修订意见回 Step 3 重出

### Step 4 — On `yes`, execute and relay the ID

Only `yes`（大小写无关，或直接说 "go / 执行 / 确认" 也算）才执行。其他回复当作修订意见，回到 Step 3 重出 plan。

**执行后必须回显 id**（让用户后续能查询 / 推送跟踪）：

| 任务类型 | 必含字段 | 示例 |
|---------|---------|------|
| Async job | `job_id` | `✅ 已提交 async job，job_id: abc123-def4-...`，附 `GET /jobs/<id>` 查询方法 |
| Pipeline | `pipeline_id` | `🔗 Pipeline 已启动，pipeline_id: xyz789-...`，附 `GET /pipelines/<id>` 查询方法 |
| Dynamic harness 创建 | 返回的 `name` | `🏭 已创建 harness，agent name: researcher-abc1` |
| 同步 `/cli` 单 call | 不需要 id | 直接展示 agent 输出 |
| Chat | 不需要 id（session_id 已在 chat-state.json） | —  |

响应里的 id **必须用完整值**（不要截短成前 8 位），方便用户复制查询。如果 Bridge 返回里没
有对应字段，说明调用失败，按错误处理。

### Step 5 — Mode recognition cheatsheet

| User says | Mode |
|-----------|------|
| User says | Mode | 典型规模 |
|-----------|------|---------|
| "先让 X 做...再让 Y 做..." | `sequence` | 2–5 步 |
| "同时问 X 和 Y" / "并行" / "多视角" | `parallel` | 2–4 个 |
| "谁快用谁" / "竞速" | `race` | 2–4 个 |
| "随便找一个" / "随机" | `random` | 2–N 候选 |
| "让 X 和 Y 讨论..." / "辩论" | `conversation` | 默认 6 轮，最多 12 |

### Step 6 — Common intent → plan quick lookup

| User intent | Suggested plan |
|-------------|---------------|
| "review 这段代码" | Single `/cli ko` — 无需 pipeline |
| "review 后再写单测" | sequence: kiro review → claude tests |
| "对比 kiro 和 claude 的方案" | parallel: kiro + claude，人工对比输出 |
| "让它们讨论一下这个设计" | conversation, 2 participants, topic = 设计主题 |
| "帮我搞个查天气的 agent" | `POST /harness` with `operator` preset |
| "让一个审代码的和一个跑测试的配合" | 批量造 2 个 harness（`reviewer` + `developer`），再 sequence 串 |
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
- [references/async-jobs.md](references/async-jobs.md) — Async jobs, target format, callback, monitoring
- [references/troubleshooting.md](references/troubleshooting.md) — Error diagnosis
- [scripts/acp-client.sh](scripts/acp-client.sh) — Bash client used by `/cli` and `/chat`
- [AGENT.md](../AGENT.md) — First-time setup guide
