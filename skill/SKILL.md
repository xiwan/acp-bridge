---
name: acp-bridge-caller
description: "通过 ACP Bridge HTTP API 调用远程 CLI agent。Usage: /cli <prompt> | /cli ko <prompt> (kiro) | /cli cc <prompt> (claude)"
---

# ACP Bridge Caller — 调用远程 CLI Agent

通过 ACP Bridge 的 HTTP API 调用远程 CLI agent（如 Kiro CLI、Claude Code 等），获取结果。

## 触发命令

| 命令 | 说明 |
|------|------|
| `/cli <prompt>` | 调用默认 agent（kiro） |
| `/cli ko <prompt>` | 调用 kiro agent |
| `/cli cc <prompt>` | 调用 claude agent |

命令映射规则：
- `/cli ko ...` → `$ACP_CLIENT -a kiro "..."`
- `/cli cc ...` → `$ACP_CLIENT -a claude "..."`
- `/cli ...` → `$ACP_CLIENT "..."`（使用默认 agent）

## 前置条件

本 skill 目录下附带 `acp-client.sh` 客户端脚本，**所有调用和响应解析都必须通过此脚本完成**。

脚本位置：与本 SKILL.md 同目录下的 `acp-client.sh`

## 参数

| 参数 | 默认值 | 必填 | 说明 |
|------|--------|------|------|
| bridge_url | 无 | 首次必填 | ACP Bridge 地址，如 `http://172.31.15.10:8001`。设置一次后整个会话内复用 |
| token | 无 | 首次必填 | 认证 token，用于 Bearer 认证。设置一次后整个会话内复用 |
| agent | 无 | 否 | 要调用的 agent 名称，通过 `-l` 查看可用列表 |
| session_id | 自动生成 | 否 | UUID 格式的会话 ID，用于隔离不同 agent 的会话和多轮对话 |
| prompt | 无 | 是 | 发送给 agent 的提示词 |

## 会话状态

- `bridge_url`：首次调用时由用户提供或从上下文推断，之后在整个会话中记住并复用
- `token`：首次调用时由用户提供，之后在整个会话中记住并复用。**不要在输出中明文显示 token**
- `session_id`：每个 agent 自动分配一个固定 UUID，整个会话内复用。切换 agent 时使用不同的 session_id，避免会话冲突
- **客户端必须自行保存 `session_id`**：首次调用后从 stderr 获取到的 session_id，客户端需要持久保存，后续所有对话都必须携带同一个 session_id。一旦 session_id 发生变化，服务端会将其视为一个全新的对话，之前的上下文将丢失
- 如果用户未提供 `bridge_url` 或 `token`，必须先询问，拿到后再执行调用

## Session ID 规则

- **必须是 UUID 格式**（如 `00000000-0000-0000-0000-000000000001`），非 UUID 会返回 422 错误
- 不指定 `-s` 时，脚本会自动按 agent 名生成确定性 UUID，确保同一 agent 始终复用同一 session
- **客户端职责**：首次调用生成 session_id 后，客户端必须自行保存并在后续每次调用中携带。session_id 一旦变化，服务端视为全新对话，之前的上下文不会延续
- 不同 agent 使用不同 session_id，避免切换 agent 时卡住或无响应
- 同一 agent 多轮对话保持同一 session_id 即可续上上下文

## 执行流程

### Step 1 — 检查认证信息

**首次调用前必须确认以下两项，缺一不可：**

1. `bridge_url` — Bridge 地址
2. `token` — 认证 token

如果用户未提供，**停下来询问**，不要尝试无 token 调用。示例提问：

> 请提供 ACP Bridge 的地址和认证 token：
> - Bridge URL（如 `http://<ip>:8001`）
> - Token（Bearer 认证用）

拿到后设置环境变量：

```bash
export ACP_BRIDGE_URL=<bridge_url>
export ACP_TOKEN=<token>
```

### Step 2 — 确认脚本可用

找到本 skill 目录下的 `acp-client.sh`，确认可执行：

```bash
ACP_CLIENT="<skill_dir>/acp-client.sh"
chmod +x "$ACP_CLIENT"
```

### Step 3 — 确认服务可用

```bash
$ACP_CLIENT -l
```

输出可用 agents 列表。常见错误：
- 连接失败 → Bridge 地址或端口不对
- `unauthorized` → token 不正确
- `forbidden` → IP 不在白名单

### Step 4 — 调用 Agent

```bash
# 单次调用（自动生成 session_id）
$ACP_CLIENT "<prompt>"

# 指定 agent
$ACP_CLIENT -a <agent> "<prompt>"

# 指定 session_id（必须是 UUID 格式）
$ACP_CLIENT -s 00000000-0000-0000-0000-000000000001 "<prompt>"
```

- stdout：agent 的回复内容（纯文本）
- stderr：`session_id`（用于多轮对话）和错误信息

### Step 5 — 多轮对话

同一 agent 的后续调用会自动复用 session_id，无需手动指定：

```bash
# 首次调用默认 agent
$ACP_CLIENT "帮我看看项目结构"

# 后续调用自动复用同一 session
$ACP_CLIENT "然后帮我写个 README"

# 切换到其他 agent（自动使用不同 session）
$ACP_CLIENT -a claude "分析一下这段代码"
```

也可以手动指定 session_id 继续对话：

```bash
$ACP_CLIENT -s <uuid> "后续问题"
```

## 安全要求

- **绝对不要在输出、日志或回复中明文显示 token**
- token 只通过环境变量 `ACP_TOKEN` 传递，不要写入命令行参数的可见输出中
- 如果调用失败提示 unauthorized，告知用户"token 可能不正确"，不要回显 token 值

## 故障处理

| 现象 | 原因 | 处理 |
|------|------|------|
| `❌ 连接失败` | 服务未启动或地址错误 | 确认 Bridge 地址和端口 |
| `unauthorized` | token 不正确 | 请用户确认 token |
| `forbidden` | IP 不在白名单 | 联系 Bridge 管理员添加 IP |
| `422 Unprocessable` | session_id 不是 UUID 格式 | 使用 UUID 格式的 session_id |
| `❌ server_error` | CLI 执行出错 | 查看错误信息定位问题 |
| 长时间无响应 | CLI 处理耗时或 session 冲突 | 确认不同 agent 使用不同 session_id |

## 注意事项

- 每次调用响应时间取决于远程 CLI（通常 3-10 秒，复杂任务更久）
- prompt 中的特殊字符由脚本自动处理 JSON 转义，无需手动处理
- 多个 parts 会被拼接为一个完整 prompt 发送给 CLI
