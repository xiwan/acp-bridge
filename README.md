# ACP Bridge

将本地 CLI agent（如 Kiro CLI、Claude Code）通过 [ACP 协议](https://agentclientprotocol.com/) HTTP API 对外暴露的桥接服务。

## 架构概览

```
┌──────────────┐    HTTP/SSE      ┌──────────────┐   ACP stdio     ┌──────────────┐
│  远程调用方   │ ──────────────▶ │  ACP Bridge   │ ──────────────▶ │  CLI Agent    │
│  (skill/插件) │ ◀── SSE 事件 ── │  (uvicorn)    │ ◀── JSON-RPC ── │  kiro/claude  │
└──────────────┘                  └──────────────┘                  └──────────────┘
                                        │
                                   IP 白名单 +
                                   Bearer Token
```

两种 agent 调用模式：
- **ACP 模式**（推荐）：`kiro-cli acp` — 通过 stdio JSON-RPC 双向通信，支持结构化事件流（thinking、tool_call、text）、进程复用、多轮上下文保持
- **PTY 模式**（fallback）：subprocess 逐行读 stdout，适用于不支持 ACP 的旧 CLI

## 功能

- ACP 协议原生支持：结构化事件流（thinking / tool_call / text / status）
- 进程池管理：同一 session 复用子进程，多轮对话上下文自动保持
- 同步 + SSE 流式双模式
- Bearer Token + IP 白名单双重认证
- 自动清理空闲 session（可配置 TTL）
- 支持多 agent 并发（kiro、claude 等）
- PTY fallback 兼容不支持 ACP 的 CLI

## 项目结构

```
acp-bridge/
├── main.py              # 入口：配置加载、进程池初始化、双模式 handler 注册
├── src/
│   ├── acp_client.py    # ACP 进程池 + JSON-RPC 连接管理
│   ├── agents.py        # agent handler（ACP 模式 + PTY fallback）
│   ├── sse.py           # ACP session/update → SSE 事件转换
│   └── security.py      # 安全中间件（IP 白名单 + Bearer Token）
├── skill/
│   ├── SKILL.md         # Kiro skill 定义
│   └── acp-client.sh    # 客户端调用脚本
├── test/
│   ├── test.sh          # 集成测试（通过 acp-client.sh 测试）
│   └── 2026-03-10.md    # 测试结果
├── config.yaml          # 服务配置
├── pyproject.toml       # Python 依赖
└── uv.lock
```

## 环境要求

- Python >= 3.12（服务端）
- [uv](https://docs.astral.sh/uv/) 包管理器
- 已安装的 CLI agent（如 `kiro-cli`、`claude-agent-acp`）
- 客户端依赖：`curl`、`jq`、`uuidgen`（大多数 Linux 发行版已预装）

## 快速开始

```bash
cd acp-bridge
cp config.yaml.example config.yaml
# 编辑 config.yaml，填入 auth_token 和 allowed_ips
uv sync
uv run main.py
```

## 配置

```yaml
server:
  host: "0.0.0.0"
  port: 8001
  session_ttl_hours: 24
  shutdown_timeout: 30

pool:
  max_processes: 20        # 全局最大子进程数
  max_per_agent: 10        # 单 agent 最大子进程数

security:
  auth_token: "${ACP_BRIDGE_TOKEN}"   # 支持环境变量引用
  allowed_ips:
    - "127.0.0.1"

agents:
  kiro:
    enabled: true
    mode: "acp"                       # ACP 协议模式
    command: "kiro-cli"
    acp_args: ["acp", "--trust-all-tools"]
    working_dir: "/tmp"
    description: "Kiro CLI agent"
  claude:
    enabled: true
    mode: "acp"
    command: "claude-agent-acp"
    acp_args: []
    working_dir: "/tmp"
    description: "Claude Code agent (via ACP adapter)"
  legacy-cli:
    enabled: false
    mode: "pty"                       # PTY fallback
    command: "some-old-cli"
    args: ["--non-interactive"]
    description: "Legacy CLI (no ACP)"
```

## 客户端调用

### acp-client.sh（推荐）

```bash
export ACP_BRIDGE_URL=http://<bridge-ip>:8001
export ACP_TOKEN=<your-token>

# 列出可用 agents
./skill/acp-client.sh -l

# 同步调用
./skill/acp-client.sh "帮我看看项目结构"

# 流式调用（SSE）
./skill/acp-client.sh --stream "分析这段代码"

# 指定 agent
./skill/acp-client.sh -a claude "hello"

# 多轮对话（同一 session_id）
./skill/acp-client.sh -s 00000000-0000-0000-0000-000000000001 "继续上面的问题"
```

### curl

```bash
# 同步
curl -X POST http://localhost:8001/runs \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer <token>" \
  -d '{"agent_name":"kiro","session_id":"<uuid>",
       "input":[{"parts":[{"content":"hello","content_type":"text/plain"}]}]}'

# 流式 SSE
curl -N -X POST http://localhost:8001/runs \
  -H "Content-Type: application/json" \
  -H "Accept: text/event-stream" \
  -H "Authorization: Bearer <token>" \
  -d '{"agent_name":"kiro","mode":"stream",
       "input":[{"parts":[{"content":"hello","content_type":"text/plain"}]}]}'
```

## SSE 事件格式

流式模式下 Bridge 输出的 SSE 事件：

| 事件 type | 含义 | 关键字段 |
|-----------|------|----------|
| `message.part` | agent 回复文本 | `part.content` |
| `message.part` (name=thought) | agent 思考过程 | `part.content`, `part.name` |
| `run.completed` | 执行完成 | `run.session_id` |
| `run.failed` | 执行失败 | `run.error.message` |

ACP 模式下额外支持的结构化事件（通过 MessagePart 传递）：

| 内容前缀 | 含义 |
|----------|------|
| `[tool.start]` | 工具调用开始 |
| `[tool.done]` | 工具调用完成 |
| `[status]` | 状态更新（如执行计划） |

## 测试

```bash
ACP_TOKEN=<token> bash test/test.sh http://127.0.0.1:8001
```

测试覆盖：列出 agents、同步调用、流式调用、多轮对话上下文保持、错误处理。

## API 端点

| 方法 | 路径 | 说明 | 认证 |
|------|------|------|------|
| GET | `/agents` | 列出已注册 agents | 需要 |
| POST | `/runs` | 调用 agent | 需要 |
| GET | `/health` | 健康检查 | 不需要 |
| GET | `/health/agents` | agent 状态 + 活跃 session 数 | 需要 |
| DELETE | `/sessions/{agent}/{session_id}` | 关闭指定 session | 需要 |

## 进程池管理

- 每个 `(agent, session_id)` 对应一个独立的 CLI ACP 子进程
- 同一 session 的多轮对话复用同一子进程，上下文自动保持
- 子进程崩溃时下次请求自动重建（上下文丢失，会提示用户）
- 空闲超过 `session_ttl_hours` 的子进程自动清理
- 超过 `max_processes` / `max_per_agent` 限制时返回 pool_exhausted 错误

## 安全

- IP 白名单 + Bearer Token 双重认证
- `/health` 免认证（供 LB 探活）
- token 支持 `${ENV_VAR}` 环境变量引用，避免明文存储
- CLI agent 以 Bridge 进程用户权限运行

## 故障排查

| 现象 | 原因 | 处理 |
|------|------|------|
| `403 forbidden` | IP 不在白名单 | 添加客户端 IP 到 `allowed_ips` |
| `401 unauthorized` | token 不正确 | 检查 Bearer token |
| `pool_exhausted` | 并发 session 超限 | 调大 `max_processes` 或清理空闲 session |
| 多轮对话上下文丢失 | 子进程崩溃后自动重建 | 正常现象，会提示用户 |
| 连接失败 | 服务未启动 | 确认 Bridge 进程在运行 |
