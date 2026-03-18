---
name: acp-bridge-caller
description: "v0.7.3 — 通过 ACP Bridge HTTP API 调用远程 CLI agent。Usage: /cli <prompt> | /cli ko <prompt> (kiro) | /cli cc <prompt> (claude) | /chat ko (进入对话模式)"
---

# ACP Bridge Caller — Invoke Remote CLI Agents

Call remote CLI agents (Kiro CLI, Claude Code, OpenAI Codex, etc.) via the [ACP Bridge](https://github.com/xiwan/acp-bridge/) HTTP API and retrieve results.

## Trigger Commands

| Command | Description |
|---------|-------------|
| `/cli <prompt>` | Call default agent (kiro) |
| `/cli ko <prompt>` | Call kiro agent |
| `/cli cc <prompt>` | Call claude agent |
| `/cli cx <prompt>` | Call codex agent |
| `/chat ko [--cwd <path>]` | 激活 kiro 进入对话模式 |
| `/chat cc [--cwd <path>]` | 激活 claude 进入对话模式 |
| `/chat end` | 退出对话模式，清空状态 |
| `/chat status` | 查看当前对话状态 |

Command mapping:
- `/cli ko ...` → `$ACP_CLIENT -a kiro "..."`
- `/cli cc ...` → `$ACP_CLIENT -a claude "..."`
- `/cli cx ...` → `$ACP_CLIENT -a codex "..."`
- `/cli ...` → `$ACP_CLIENT "..."` (uses default agent)

## Message Routing Priority

收到用户消息时，按以下优先级依次匹配，命中即停止：

1. **第一优先：`/chat` 命令** — 消息以 `/chat` 开头 → 走 Chat 命令处理（`/chat ko`、`/chat cc`、`/chat end`、`/chat status`）
2. **第二优先：`/cli` 命令** — 消息以 `/cli` 开头 → 走 CLI 单次调用
3. **第三优先：Chat 对话透传** — `chat-state.json` 存在且 `active_agent` 非空 → 将整条消息作为 prompt 自动透传给当前 active agent（复用 state 中的 session_id 和 cwd）
4. **兜底** — 以上均不匹配 → 正常处理（不调用 ACP Bridge）

## Chat Mode

Chat 模式允许激活一个 agent 后，后续消息自动透传给该 agent，无需每次输入 `/cli` 前缀。

### 状态文件

状态文件 `chat-state.json` 存放在本 skill 目录（与 SKILL.md 同级），JSON 格式：

```json
{
  "active_agent": "kiro",
  "session_id": "00000000-0000-0000-0000-000000000001",
  "cwd": "/home/ec2-user/projects/acp-bridge",
  "started_at": "2025-01-15T10:30:00Z"
}
```

| Field | Description |
|-------|-------------|
| `active_agent` | 当前激活的 agent 名称（`kiro`、`claude` 等） |
| `session_id` | 该 agent 使用的 session UUID，用于多轮对话 |
| `cwd` | 工作目录，传递给 acp-client.sh 的 `--cwd` 参数 |
| `started_at` | 激活时间（ISO 8601） |

### `/chat ko` / `/chat cc` — 激活对话模式

```bash
# 激活 kiro，使用默认工作目录
/chat ko

# 激活 kiro，指定工作目录
/chat ko --cwd /home/ec2-user/projects/acp-bridge

# 激活 claude
/chat cc
```

处理流程：
1. 为该 agent 生成或复用一个固定的 session_id（与 `/cli` 模式相同的 UUID 生成规则）
2. 将 `active_agent`、`session_id`、`cwd`、`started_at` 写入 `chat-state.json`
3. 回复用户确认激活，例如：`🟢 已进入 kiro 对话模式 (session: xxx)`

**切换 agent**：如果当前已有 active agent，直接替换 `chat-state.json` 中的状态。旧 session 不销毁（session_id 保留在服务端），后续可通过 `/cli -s <old-session-id>` 恢复。

### `/chat end` — 退出对话模式

删除 `chat-state.json`（或清空 `active_agent`），回复：`🔴 已退出对话模式`

### `/chat status` — 查看状态

读取 `chat-state.json`，输出：

```
📊 Chat 状态
- Agent: kiro
- Session: 00000000-0000-0000-0000-000000000001
- 工作目录: /home/ec2-user/projects/acp-bridge
- 已激活: 15 分钟
```

如果没有激活的对话，输出：`ℹ️ 当前无活跃对话`

### 对话透传

当 `chat-state.json` 存在且 `active_agent` 非空时，用户发送的非 `/chat`、非 `/cli` 消息自动透传：

```bash
$ACP_CLIENT -a <active_agent> -s <session_id> "<用户消息>"
```

如果 state 中有 `cwd`，则附加 `--cwd` 参数。

## Prerequisites

This skill directory includes the `acp-client.sh` client script. **All calls and response parsing must go through this script.**

Script location: `acp-client.sh` in the same directory as this SKILL.md.

## Parameters

| Parameter | Default | Required | Description |
|-----------|---------|----------|-------------|
| bridge_url | — | Yes (first call) | ACP Bridge address, e.g. `http://172.31.15.10:8001`. Reused for the entire session once set |
| token | — | Yes (first call) | Auth token for Bearer authentication. Reused for the entire session once set |
| agent | — | No | Agent name to call. Use `-l` to list available agents |
| session_id | Auto-generated | No | UUID-format session ID for isolating agent sessions and multi-turn conversations |
| prompt | — | Yes | Prompt to send to the agent |

## Session State

- `bridge_url`: Provided by user or inferred from context on first call, then remembered and reused for the entire session
- `token`: Provided by user on first call, then remembered and reused. **Never display the token in plaintext output**
- `session_id`: Each agent is automatically assigned a fixed UUID, reused throughout the session. Different agents use different session_ids to avoid conflicts
- **The client must persist `session_id`**: After the first call, capture the session_id from stderr and reuse it in all subsequent calls. If the session_id changes, the server treats it as a brand new conversation and previous context is lost
- If the user has not provided `bridge_url` or `token`, ask for them before making any calls

## Session ID Rules

- **Must be UUID format** (e.g. `00000000-0000-0000-0000-000000000001`). Non-UUID values return a 422 error
- When `-s` is not specified, the script auto-generates a deterministic UUID based on the agent name, ensuring the same agent always reuses the same session
- **Client responsibility**: After generating a session_id on the first call, the client must persist it and include it in every subsequent call. If the session_id changes, the server treats it as a new conversation with no prior context
- Different agents use different session_ids to avoid hanging or unresponsive behavior when switching agents
- Same agent multi-turn conversations keep the same session_id to maintain context

## Execution Flow

### Step 1 — Verify Auth Info

**Before the first call, confirm both of the following — both are required:**

1. `bridge_url` — Bridge address
2. `token` — Auth token

If the user hasn't provided them, **stop and ask**. Do not attempt a call without a token. Example prompt:

> Please provide the ACP Bridge address and auth token:
> - Bridge URL (e.g. `http://<ip>:8001`)
> - Token (for Bearer authentication)

Once obtained, set environment variables:

```bash
export ACP_BRIDGE_URL=<bridge_url>
export ACP_TOKEN=<token>
```

### Step 2 — Confirm Script Availability

Locate `acp-client.sh` in this skill directory and ensure it's executable:

```bash
ACP_CLIENT="<skill_dir>/acp-client.sh"
chmod +x "$ACP_CLIENT"
```

### Step 3 — Verify Service Availability

```bash
$ACP_CLIENT -l
```

Outputs the list of available agents. Common errors:
- Connection failed → Wrong Bridge address or port
- `unauthorized` → Incorrect token
- `forbidden` → IP not in allowlist

### Step 4 — Call an Agent

```bash
# Single call (auto-generated session_id)
$ACP_CLIENT "<prompt>"

# Specify agent
$ACP_CLIENT -a <agent> "<prompt>"

# Specify session_id (must be UUID format)
$ACP_CLIENT -s 00000000-0000-0000-0000-000000000001 "<prompt>"
```

- stdout: Agent's reply (plain text)
- stderr: `session_id` (for multi-turn conversations) and error messages

### Step 5 — Multi-turn Conversations

Subsequent calls to the same agent automatically reuse the session_id:

```bash
# First call to default agent
$ACP_CLIENT "Show me the project structure"

# Follow-up call automatically reuses the same session
$ACP_CLIENT "Now write a README"

# Switch to another agent (automatically uses a different session)
$ACP_CLIENT -a claude "Analyze this code"
```

You can also manually specify a session_id to continue a conversation:

```bash
$ACP_CLIENT -s <uuid> "Follow-up question"
```

## Security Requirements

- **Never display the token in plaintext in output, logs, or replies**
- Pass the token only via the `ACP_TOKEN` environment variable; do not expose it in visible command-line output
- If a call fails with unauthorized, tell the user "the token may be incorrect" — do not echo the token value

## Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| `❌ Connection failed` | Service not running or wrong address | Verify Bridge address and port |
| `unauthorized` | Incorrect token | Ask user to confirm token |
| `forbidden` | IP not in allowlist | Contact Bridge admin to add IP |
| `422 Unprocessable` | session_id is not UUID format | Use a UUID-format session_id |
| `❌ server_error` | CLI execution error | Check error message for details |
| Long timeout | CLI processing or session conflict | Ensure different agents use different session_ids |

## Notes

- Response time depends on the remote CLI (typically 3–10s, longer for complex tasks)
- Special characters in prompts are automatically JSON-escaped by the script
- Multiple parts are concatenated into a single prompt sent to the CLI

## Async Job Mode

For long-running tasks, submit asynchronously and get results pushed to Discord or Feishu on completion.

### Submit an Async Job

Async mode requires callback info. **Call the `/jobs` endpoint directly** — do not use `acp-client.sh --async`.

When an OpenClaw agent receives a message, it can obtain channel and account info from the current session's `deliveryContext`:

#### Discord Example

```bash
curl -X POST "$ACP_BRIDGE_URL/jobs" \
  -H "Authorization: Bearer $ACP_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "agent_name": "<agent>",
    "prompt": "<user prompt>",
    "target": "<deliveryContext.to>",
    "channel": "discord",
    "callback_meta": {
      "account_id": "<deliveryContext.accountId>"
    }
  }'
```

#### Feishu Example

```bash
curl -X POST "$ACP_BRIDGE_URL/jobs" \
  -H "Authorization: Bearer $ACP_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "agent_name": "<agent>",
    "prompt": "<user prompt>",
    "target": "user:<feishu-open-id>",
    "channel": "feishu",
    "callback_meta": {
      "account_id": "main"
    }
  }'
```

#### With Custom Working Directory

```bash
curl -X POST "$ACP_BRIDGE_URL/jobs" \
  -H "Authorization: Bearer $ACP_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "agent_name": "kiro",
    "prompt": "Analyze this project",
    "cwd": "/home/user/projects/my-app",
    "target": "<target>",
    "channel": "discord"
  }'
```

Returns: `{"job_id": "xxx", "status": "pending"}`

The agent should immediately reply to the user: "✅ Submitted. Results will be pushed automatically when done."

### Query Job Status

```bash
curl -H "Authorization: Bearer $ACP_TOKEN" "$ACP_BRIDGE_URL/jobs/<job_id>"
```

### Auto-push on Completion

When an async job completes, Bridge automatically sends results to the specified IM channel via OpenClaw Gateway.

**For push to work, the following must be included when submitting:**

| Field | Source | Description |
|-------|--------|-------------|
| `target` | `deliveryContext.to` | Discord: `channel:<id>` or `user:<id>`. Feishu: `user:<open_id>` or `<chat_id>` |
| `channel` | IM platform | `discord`, `feishu`, etc. Defaults to `discord` if omitted |
| `callback_meta.account_id` | Bot account ID | `default` for Discord, `main` for Feishu (depends on OpenClaw config) |

**Important:**
- `account_id` is the OpenClaw-configured bot account, usually `default` for Discord, `main` for Feishu — not the agent name
- Discord DM channels must use `user:<user_id>` format, not `channel:<dm_channel_id>`
- `discord_target` is still accepted for backward compatibility but `target` is preferred

**Consequences of missing fields:**

| Missing | Result |
|---------|--------|
| `target` | Job runs normally but results are not pushed (webhook payload falls back to raw JSON) |
| `account_id` | Webhook reaches OpenClaw but lacks bot identity; OpenClaw cannot route, returns 500 |
| Both | Job runs normally; results can only be retrieved via `GET /jobs/{id}` |

### Monitoring

```bash
# View all job statuses
curl -H "Authorization: Bearer $ACP_TOKEN" "$ACP_BRIDGE_URL/jobs"
```

Returns all jobs + status stats (pending/running/completed/failed). Jobs running longer than 10 minutes are auto-marked as failed and trigger a callback notification.
