---
name: acp-bridge-caller
description: "v0.8.1 — 通过 ACP Bridge HTTP API 调用远程 CLI agent。Usage: /cli <prompt> | /cli ko <prompt> (kiro) | /cli cc <prompt> (claude) | /chat ko (进入对话模式)"
disable-model-invocation: true
---

# ACP Bridge Caller

Call remote CLI agents via [ACP Bridge](https://github.com/xiwan/acp-bridge/) HTTP API.

All calls go through `acp-client.sh` in this skill directory:

```bash
ACP_CLIENT="${CLAUDE_SKILL_DIR}/acp-client.sh"
chmod +x "$ACP_CLIENT"
```

## Auth Setup

Before the first call, both `ACP_BRIDGE_URL` and `ACP_TOKEN` are required. If not set, **stop and ask the user**.

```bash
export ACP_BRIDGE_URL=<bridge_url>
export ACP_TOKEN=<token>
```

**Never display the token in plaintext.** If unauthorized, tell the user "the token may be incorrect".

## Commands

| Command | Action |
|---------|--------|
| `/cli <prompt>` | `$ACP_CLIENT "<prompt>"` |
| `/cli ko <prompt>` | `$ACP_CLIENT -a kiro "<prompt>"` |
| `/cli cc <prompt>` | `$ACP_CLIENT -a claude "<prompt>"` |
| `/cli cx <prompt>` | `$ACP_CLIENT -a codex "<prompt>"` |
| `/chat ko [--cwd <path>]` | 激活 kiro 对话模式 |
| `/chat cc [--cwd <path>]` | 激活 claude 对话模式 |
| `/chat end` | 退出对话模式 |
| `/chat status` | 查看对话状态 |

## Message Routing

按优先级匹配，命中即停：

1. `/chat` 开头 → Chat 命令
2. `/cli` 开头 → CLI 单次调用
3. `chat-state.json` 存在且 `active_agent` 非空 → 透传给当前 agent
4. 兜底 → 正常处理（不调用 Bridge）

## Chat Mode

激活后，后续消息自动透传给该 agent，无需 `/cli` 前缀。

**激活**：`/chat ko` 或 `/chat cc [--cwd <path>]`
1. 生成确定性 session_id（UUID v5，同 `/cli` 模式）
2. 写入 `chat-state.json`（`active_agent`、`session_id`、`cwd`、`started_at`）
3. 回复：`🟢 已进入 kiro 对话模式 (session: xxx)`

**透传**：`$ACP_CLIENT -a <active_agent> -s <session_id> [--cwd <cwd>] "<用户消息>"`

**退出**：`/chat end` → 删除 `chat-state.json`，回复：`🔴 已退出对话模式`

**状态**：`/chat status` → 读取 `chat-state.json` 输出 agent、session、cwd、已激活时长

**切换**：直接 `/chat cc` 替换状态，旧 session 保留在服务端。

## Session ID

- 脚本自动生成确定性 UUID（基于 agent 名），同一 agent 始终复用同一 session
- 不同 agent 使用不同 session_id，避免冲突
- 手动指定：`$ACP_CLIENT -s <uuid> "<prompt>"`

## Additional Resources

- For async jobs, API details, and target formats, see [reference.md](reference.md)
- For error troubleshooting, see [troubleshooting.md](troubleshooting.md)
