# ACP Bridge: Conversation Mode Spec

> Version: 0.2.0 (Draft)  
> Date: 2026-04-02  
> Author: Mexico 🌮 + xiwan27

---

## 概述

在现有 Pipeline 模式（sequence/parallel/race/random）基础上，新增 **Conversation Mode**，让多个 Agent 能够进行多轮对话协作。

### 核心原则

**Bridge 是传话人，不是会议记录员。**

- Agent CLI 自身维护多轮上下文（进程池保持 session）
- Bridge 只负责把"对方刚说的话"传给下一个 agent
- Bridge 用 SQLite 记录完整 transcript，供用户查询和 webhook 推送
- Transcript 不会塞进 prompt

### 目标

1. Agent 之间可以多轮交流
2. 支持 @mention 定向传话
3. 对话有明确的终止条件
4. 完整对话记录可查询

### 非目标

- 不修改 Agent CLI 本身
- 不实现 Agent 直接点对点通信（仍通过 Bridge 中转）
- 不在 prompt 中注入完整 transcript

---

## 架构

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                              Conversation Flow                                  │
└─────────────────────────────────────────────────────────────────────────────────┘

  Bridge 角色：传话 + 记账

  kiro 进程 (有自己的完整记忆)          claude 进程 (有自己的完整记忆)
  ┌─────────────────────────┐          ┌─────────────────────────┐
  │ turn 1: 我说了 A        │          │ turn 2: 我说了 B        │
  │ turn 3: 我说了 C        │          │ turn 4: 我说了 D        │
  └─────────────────────────┘          └─────────────────────────┘

  Bridge 传的内容：
    → kiro:   "topic + context + A2A rules"（首轮）
    ← kiro:   输出 A
    → claude: "[kiro]: A"（只传 kiro 刚说的）
    ← claude: 输出 B
    → kiro:   "[claude]: B"（只传 claude 刚说的）
    ...

  SQLite conversation_log：记录每一轮的完整内容，供 API 查询
```

---

## 调度规则

### 默认：Round-Robin

```
participants: [kiro, claude, codex]
→ kiro → claude → codex → kiro → claude → ...
```

### @mention：定向传话

Agent 回复中包含 `@agent_name` 时，下一轮跳到被 @ 的 agent：

```
kiro: "方案 A 怎么样？@codex 你觉得性能行吗？"
→ 跳过 claude，直接传给 codex

codex: "性能没问题，LGTM"
→ 没有 @mention，回到 round-robin，下一个是 claude
```

解析规则：`@(\w+)` 匹配，命中 participants 列表则定向。多个 @mention 取第一个。

---

## API 设计

### Endpoint

```
POST /pipelines
```

### Request

```json
{
  "mode": "conversation",
  "participants": ["kiro", "claude"],
  "topic": "Review and fix the null pointer bug in auth.py",
  "initial_context": "def login(user):\n    return user.name  # line 23: potential NPE",
  "config": {
    "max_turns": 10,
    "turn_timeout_seconds": 120,
    "stop_conditions": ["DONE", "CONSENSUS", "NO_PROGRESS"],
    "no_progress_threshold": 2,
    "a2a_rules": true
  }
}
```

### Parameters

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `mode` | string | Yes | - | `"conversation"` |
| `participants` | string[] | Yes | - | Agent names (2-5) |
| `topic` | string | Yes | - | Conversation topic/goal |
| `initial_context` | string | No | `""` | Shared context (code, docs, etc.) |
| `config.max_turns` | int | No | `10` | Max total turns |
| `config.turn_timeout_seconds` | int | No | `120` | Timeout per turn |
| `config.stop_conditions` | string[] | No | `["DONE"]` | Termination signals |
| `config.no_progress_threshold` | int | No | `2` | Consecutive PASS before stop |
| `config.a2a_rules` | bool | No | `true` | Inject A2A rules in first turn |

### Response (Immediate)

```json
{
  "pipeline_id": "conv-a1b2c3d4",
  "mode": "conversation",
  "status": "running",
  "participants": ["kiro", "claude"]
}
```

### Response (Completed — via GET /pipelines/{id})

```json
{
  "pipeline_id": "conv-a1b2c3d4",
  "mode": "conversation",
  "status": "completed",
  "stop_reason": "CONSENSUS",
  "turns": 6,
  "duration": 45.2,
  "transcript": [
    {"turn": 1, "agent": "kiro", "content": "...", "duration": 5.2},
    {"turn": 2, "agent": "claude", "content": "...", "duration": 8.1},
    {"turn": 3, "agent": "kiro", "content": "...", "duration": 4.8}
  ]
}
```

---

## Prompt 策略

### 首轮 Prompt（第一个 agent）

```
[CONVERSATION]
Topic: {topic}
Participants: {participant_list_with_descriptions}
You are: {current_agent}

{initial_context}

[A2A RULES]
1. You are communicating with other AI agents, not humans — skip pleasantries
2. Use structured keywords: ANALYSIS / PROPOSAL / FIX / RESPONSE / QUESTION
3. Address others with @{agent_name}
4. Say "STATUS: DONE" when task is complete
5. Say "STATUS: CONSENSUS" when all agree
6. Say "PASS" if nothing to add
7. NO "thank you" / "great point" / repeating what others said
```

### 后续轮 Prompt

只传上一个 agent 的回复：

```
[{previous_agent}]: {previous_output}
```

就这么短。Agent CLI 自己有 session 记忆，知道之前聊了什么。

### Session Reset 时

如果 agent 进程被回收（LRU 淘汰），重新发送首轮 prompt + 简要说明：

```
[CONVERSATION RESUMED]
Topic: {topic}
Participants: ...
(Your previous session was reset. Continuing from turn {N}.)

[{previous_agent}]: {previous_output}
```

---

## 终止条件

| 条件 | 触发方式 | 说明 |
|------|----------|------|
| `DONE` | Agent 输出含 `STATUS: DONE` | 任务完成 |
| `CONSENSUS` | Agent 输出含 `STATUS: CONSENSUS` | 达成共识 |
| `NO_PROGRESS` | 连续 N 轮 PASS | 无人有新内容 |
| `MAX_TURNS` | 达到 max_turns | 硬上限 |
| `TIMEOUT` | 单轮超时 | Agent 无响应 |

```python
def check_stop(content: str, config) -> str | None:
    upper = content.upper()
    if "STATUS: DONE" in upper:
        return "DONE"
    if "STATUS: CONSENSUS" in upper:
        return "CONSENSUS"
    return None

def is_pass(content: str) -> bool:
    return content.strip().upper() in ["PASS", "NOTHING TO ADD", ""]
```

---

## 错误处理

| 场景 | 处理 |
|------|------|
| Agent 单轮超时 | 记录 `[TIMEOUT]`，跳到下一个 agent |
| Agent 报错 | 重试一次，失败则跳过 |
| 所有 agent 连续 PASS | 触发 NO_PROGRESS 终止 |
| Agent 进程被回收 | 重发首轮 context，继续对话 |

---

## 存储

### SQLite conversation_log 表

```sql
CREATE TABLE conversation_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pipeline_id TEXT NOT NULL,
    turn INTEGER NOT NULL,
    agent TEXT NOT NULL,
    content TEXT NOT NULL,
    duration REAL,
    created_at REAL NOT NULL
);
CREATE INDEX idx_conv_pipeline ON conversation_log(pipeline_id);
```

复用现有 `data/jobs.db`，新增表即可。

---

## 实现计划

### P0（最小可用）

- [ ] 基础对话循环（round-robin）
- [ ] 首轮注入 topic + context + A2A rules
- [ ] 后续只传上一个 agent 的回复
- [ ] DONE/CONSENSUS/PASS 终止检测
- [ ] SQLite conversation log
- [ ] Webhook 每轮推送

### P1

- [ ] @mention 解析 + 定向调度
- [ ] Session reset 时重发 context
- [ ] 对话结果自动总结（额外一轮）

### P2

- [ ] 人类介入（human-in-the-loop）
- [ ] 中途加入/移除 agent
- [ ] 对话分叉（fork conversation）

---

## 与现有模式对比

| 模式 | 方向 | 轮数 | Prompt 策略 | 适用场景 |
|------|------|------|-------------|----------|
| `sequence` | 单向 | N (固定) | 上一步输出作为输入 | 流水线 |
| `parallel` | 单向 | 1 | 相同 prompt | 并行 |
| `race` | 单向 | 1 | 相同 prompt | 竞速 |
| `random` | 单向 | 1 | 相同 prompt | 随机选一 |
| `conversation` | 双向 | 动态 | 只传对方刚说的 | 协作讨论 |

---

## 使用示例

### 代码审查

```bash
curl -X POST "$ACP_BRIDGE_URL/pipelines" \
  -H "Authorization: Bearer $ACP_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "mode": "conversation",
    "participants": ["kiro", "claude"],
    "topic": "Review the auth module for security issues",
    "initial_context": "def login(user):\n    return user.name",
    "config": {"max_turns": 8}
  }'
```

### 三方技术讨论

```bash
curl -X POST "$ACP_BRIDGE_URL/pipelines" \
  -H "Authorization: Bearer $ACP_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "mode": "conversation",
    "participants": ["kiro", "claude", "codex"],
    "topic": "Design a rate limiting solution",
    "config": {"max_turns": 12, "stop_conditions": ["CONSENSUS"]}
  }'
```

---

## Changelog

- 2026-04-02 v0.2.0: 重新设计 — Bridge 只传话不注入 transcript，agent CLI 自维护上下文，SQLite 记录对话日志，@mention 定向调度
- 2026-04-02 v0.1.0: Initial draft

---

*Spec by Mexico 🌮 for ACP Bridge*
