# ACP Agent 接入规范

本文档定义了 CLI agent 接入 ACP Bridge 所需实现的接口。

## 线路协议

- **传输**: stdio（stdin 接收请求，stdout 发送响应/通知）
- **格式**: 换行分隔的 JSON-RPC 2.0，每条消息占一行
- **编码**: UTF-8
- **错误输出**: stderr 仅用于调试日志，Bridge 会忽略

## 接入方式

### ACP 模式（推荐）

实现本规范所有必选接口，在 `config.yaml` 中配置 `mode: "acp"`：

```yaml
agents:
  my-agent:
    enabled: true
    mode: "acp"
    command: "my-agent-cli"
    acp_args: ["acp"]          # 启动 ACP 模式的参数
    working_dir: "/tmp"
    description: "My agent"
```

特性：进程复用、多轮上下文、结构化事件（thinking / tool / status）。

### PTY 模式（兼容模式）

不支持 ACP 协议的 agent，配置 `mode: "pty"`。Bridge 每次新建子进程，逐行读取 stdout。
无进程复用，无多轮上下文，无结构化事件。

---

## 必选接口

### 1. initialize

Bridge 启动子进程后立即发送，用于握手。

**请求**
```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "initialize",
  "params": {
    "protocolVersion": 1,
    "clientCapabilities": {},
    "clientInfo": {"name": "acp-bridge", "version": "0.7.1"}
  }
}
```

**响应**
```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "result": {
    "agentInfo": {"name": "my-agent", "version": "1.0.0"},
    "capabilities": {}
  }
}
```

---

### 2. session/new

创建一个新的对话 session。

**请求**
```json
{
  "jsonrpc": "2.0",
  "id": 2,
  "method": "session/new",
  "params": {
    "cwd": "/home/user/projects",
    "mcpServers": []
  }
}
```

**响应**
```json
{
  "jsonrpc": "2.0",
  "id": 2,
  "result": {
    "sessionId": "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
  }
}
```

---

### 3. session/prompt

向 session 发送用户 prompt，流式返回通知，最后发送 JSON-RPC 响应。

**请求**
```json
{
  "jsonrpc": "2.0",
  "id": 3,
  "method": "session/prompt",
  "params": {
    "sessionId": "<session_id>",
    "prompt": [{"type": "text", "text": "用户输入内容"}]
  }
}
```

**执行中发送通知**（见下方通知类型）

**最终响应**（执行完成后）
```json
{
  "jsonrpc": "2.0",
  "id": 3,
  "result": {
    "sessionId": "<session_id>",
    "stopReason": "end_turn"
  }
}
```

---

## 可选接口

### ping

Bridge 用于健康检测，每 60s 探测一次。返回任意响应（包括 error）均视为存活。

```json
{"jsonrpc":"2.0","id":4,"method":"ping","params":{}}
→ {"jsonrpc":"2.0","id":4,"result":{}}
```

### session/cancel

Bridge 通知 agent 取消当前执行（通知，无需响应）。

```json
{"jsonrpc":"2.0","method":"session/cancel","params":{"sessionId":"..."}}
```

---

## 通知类型

执行 `session/prompt` 期间，agent 通过以下格式发送通知：

```json
{
  "jsonrpc": "2.0",
  "method": "session/update",
  "params": {
    "sessionId": "<session_id>",
    "update": { ... }
  }
}
```

Bridge 能识别的 `update` 类型：

| sessionUpdate | 说明 | 必要字段 |
|---|---|---|
| `agent_message_chunk` | 正文输出（流式） | `content.text` |
| `agent_thought_chunk` | 思考过程 | `content.text` |
| `tool_call` | 工具调用开始 | `toolCallId`, `title`, `status` |
| `tool_call_update` | 工具调用更新/完成 | `toolCallId`, `title`, `status` |
| `plan` | 执行计划说明 | `entries[].content` |

**示例：正文输出**
```json
{
  "jsonrpc": "2.0",
  "method": "session/update",
  "params": {
    "sessionId": "...",
    "update": {
      "sessionUpdate": "agent_message_chunk",
      "content": {"text": "这是输出内容"}
    }
  }
}
```

---

## Bridge 发送给 agent 的通知

### session/request_permission

当 agent 需要用户授权某项操作时发送此通知。Bridge 会自动回复 `proceed_always`，无需 agent 等待。

```json
{"jsonrpc":"2.0","id":99,"method":"session/request_permission","params":{...}}
→ {"jsonrpc":"2.0","id":99,"result":{"outcome":{"outcome":"selected","optionId":"proceed_always"}}}
```

---

## 合规测试

直接对 agent 二进制做 stdio 测试，不需要启动 Bridge：

```bash
bash test/test_agent_compliance.sh <command> [args...]

# 示例
bash test/test_agent_compliance.sh kiro-cli acp --trust-all-tools
bash test/test_agent_compliance.sh claude-agent-acp
bash test/test_agent_compliance.sh python examples/echo-agent.py
```

### 测试项

| ID | 项目 | 级别 |
|----|------|------|
| T1.1 | initialize 返回 result | 必选 |
| T1.2 | initialize 包含 agentInfo | 必选 |
| T2.1 | session/new 返回 result | 必选 |
| T2.2 | session/new 包含 sessionId | 必选 |
| T3.1 | session/prompt 发送 agent_message_chunk 通知 | 必选 |
| T3.2 | session/prompt 返回最终 result | 必选 |
| T3.3 | session/prompt result 包含 stopReason | 必选 |
| T4 | ping 有响应 | 可选 |

T1–T3 全部通过即视为**合规**，可接入 ACP Bridge ACP 模式。

---

## 参考实现

`examples/echo-agent.py` — 100 行以内的最小合规实现，实现全部必选接口，prompt 原文 echo 返回。
