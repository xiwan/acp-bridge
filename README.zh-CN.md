```
╔══════════════════════════════════════════════════════════════╗
║                                                              ║
║     _   ___ ___   ___      _    _                            ║
║    /_\ / __| _ \ | _ )_ __(_)__| |__ _  ___                  ║
║   / _ \ (__| _/  | _ \ '_|| / _` / _` |/ -_)                 ║
║  /_/ \_\___|_|   |___/|_| |_\__,_\__, \___|                  ║
║                                   |___/                      ║
╠══════════════════════════════════════════════════════════════╣
║                                                              ║
║    🤖 Kiro ───┐                                              ║
║                ├──► acp 🌉 ──► 🦞 OpenClaw ──► 🌍 world     ║
║    🤖 Claude ──┘                                             ║
║                                                              ║
║          https://github.com/xiwan/acp-bridge                 ║
╚══════════════════════════════════════════════════════════════╝

        ~ Local AI agents 🔌 ACP protocol 🦞 The world ~
```

# ACP Bridge

[English](README.md)

将本地 CLI agent（如 Kiro CLI、Claude Code）通过 [ACP 协议](https://agentclientprotocol.com/) HTTP API 对外暴露的桥接服务，支持异步任务和 Discord 推送。

## 架构概览

```
┌──────────┐            ┌──────────┐  HTTP JSON req     ┌──────────────┐  ACP stdio     ┌──────────────┐
│ Discord  │◀──────────▶│ OpenClaw │──────────────────▶│  ACP Bridge  │──────────────▶│  CLI Agent   │
│ 用户     │  Discord   │ Gateway  │◀──── SSE stream ───│  (uvicorn)   │◀── JSON-RPC ──│  kiro/claude │
└──────────┘            └──────────┘◀── /tools/invoke ──└──────────────┘               └──────────────┘
                                      (async job push)
```

两种调用模式：
- **同步/流式**：skill 通过 `acp-client.sh` 调用，等待结果
- **异步 job**：提交任务立即返回，完成后通过 OpenClaw 回调推送到 Discord

两种 agent 模式：
- **ACP 模式**（推荐）：stdio JSON-RPC 双向通信，结构化事件流，进程复用
- **PTY 模式**（fallback）：subprocess 逐行读 stdout，兼容旧 CLI

## 功能

- ACP 协议原生支持：结构化事件流（thinking / tool_call / text / status）
- 进程池管理：同一 session 复用子进程，多轮对话上下文自动保持
- 同步 + SSE 流式 + Markdown card 输出
- 异步任务：提交即返回，完成后 webhook 回调
- Discord 推送：通过 OpenClaw Gateway `/tools/invoke` 发送结果
- Job 监控：卡住检测（>10min 自动标记失败）、webhook 重试、状态统计
- 自动回复 `session/request_permission`（claude 不卡住）
- Bearer Token + IP 白名单双重认证
- 客户端纯 bash + jq，零 python 依赖

## 项目结构

```
acp-bridge/
├── main.py              # 入口：进程池、handler 注册、job/health 端点
├── src/
│   ├── acp_client.py    # ACP 进程池 + JSON-RPC 连接管理
│   ├── agents.py        # agent handler（ACP 模式 + PTY fallback）
│   ├── jobs.py          # 异步任务管理器（提交、监控、webhook 回调）
│   ├── sse.py           # ACP session/update → SSE 事件转换
│   └── security.py      # 安全中间件（IP 白名单 + Bearer Token）
├── skill/
│   ├── SKILL.md         # Kiro/OpenClaw skill 定义
│   └── acp-client.sh    # 客户端调用脚本（bash + jq）
├── test/
│   ├── test.sh          # 集成测试
│   └── 2026-03-10.md    # 测试结果
├── config.yaml          # 服务配置
├── pyproject.toml
└── uv.lock
```

## 环境要求

- Python >= 3.12（服务端）
- [uv](https://docs.astral.sh/uv/) 包管理器
- 已安装的 CLI agent（如 `kiro-cli`、`claude-agent-acp`）
- 客户端依赖：`curl`、`jq`、`uuidgen`

## 快速开始

```bash
cd acp-bridge
cp config.yaml.example config.yaml
# 编辑 config.yaml
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
  max_processes: 20
  max_per_agent: 10

webhook:
  url: "http://<openclaw-ip>:18789/tools/invoke"
  token: "<OPENCLAW_GATEWAY_TOKEN>"

security:
  auth_token: "${ACP_BRIDGE_TOKEN}"
  allowed_ips:
    - "127.0.0.1"

agents:
  kiro:
    enabled: true
    mode: "acp"
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
```

## 客户端调用

### acp-client.sh

```bash
export ACP_BRIDGE_URL=http://<bridge-ip>:8001
export ACP_TOKEN=<your-token>

# 列出可用 agents
./skill/acp-client.sh -l

# 同步调用
./skill/acp-client.sh "帮我看看项目结构"

# 流式调用
./skill/acp-client.sh --stream "分析这段代码"

# Markdown 卡片输出（适合 IM 展示）
./skill/acp-client.sh --card -a kiro "介绍你自己"

# 指定 agent
./skill/acp-client.sh -a claude "hello"

# 多轮对话
./skill/acp-client.sh -s 00000000-0000-0000-0000-000000000001 "继续"
```

## 异步任务 + Discord 推送

提交耗时任务，完成后自动推送结果到 Discord。

### 提交

```bash
curl -X POST http://<bridge>:8001/jobs \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{
    "agent_name": "kiro",
    "prompt": "帮我重构模块",
    "discord_target": "user:614812830620975105",
    "callback_meta": {"account_id": "default"}
  }'
# → {"job_id": "xxx", "status": "pending"}
```

### 查询

```bash
curl http://<bridge>:8001/jobs/<job_id> \
  -H "Authorization: Bearer <token>"
```

### 回调流程

```
POST /jobs → Bridge 后台执行 → 完成后 POST OpenClaw /tools/invoke
  → OpenClaw 用 message tool 发到 Discord → 用户收到结果
```

### discord_target 格式

| 场景 | 格式 | 示例 |
|------|------|------|
| 服务器频道 | `channel:<id>` 或 `#name` | `channel:1477514611317145732` |
| DM 私信 | `user:<user_id>` | `user:614812830620975105` |

`account_id` 是 OpenClaw 的 Discord bot account（通常是 `default`），不是 agent name。

### Job 监控

- `GET /jobs` — 列出所有 job + 状态统计
- 每 60s 巡查：卡住 >10min 自动标记 failed + 通知
- webhook 发送失败自动重试，直到成功或 job 过期清理

## API 端点

| 方法 | 路径 | 说明 | 认证 |
|------|------|------|------|
| GET | `/agents` | 列出已注册 agents | 需要 |
| POST | `/runs` | 同步/流式调用 agent | 需要 |
| POST | `/jobs` | 提交异步任务 | 需要 |
| GET | `/jobs` | 列出所有 job + 统计 | 需要 |
| GET | `/jobs/{job_id}` | 查询单个 job | 需要 |
| GET | `/health` | 健康检查 | 不需要 |
| GET | `/health/agents` | agent 状态 | 需要 |
| DELETE | `/sessions/{agent}/{session_id}` | 关闭 session | 需要 |

## 测试

```bash
ACP_TOKEN=<token> bash test/test.sh http://127.0.0.1:8001
```

覆盖：agents 列表、同步/流式调用、多轮对话、Claude、异步任务、错误处理。

## 进程池管理

- 每个 `(agent, session_id)` 对应独立的 CLI ACP 子进程
- 同一 session 多轮对话复用子进程，上下文自动保持
- 子进程崩溃自动重建（上下文丢失，提示用户）
- 空闲超过 TTL 自动清理
- `session/request_permission` 自动回复 `allow_always`（claude 兼容）

## 安全

- IP 白名单 + Bearer Token 双重认证
- `/health` 免认证（LB 探活）
- token 支持 `${ENV_VAR}` 环境变量引用
- webhook token 独立配置，与 Bridge auth token 隔离

## 故障排查

| 现象 | 原因 | 处理 |
|------|------|------|
| `403 forbidden` | IP 不在白名单 | 添加 IP 到 `allowed_ips` |
| `401 unauthorized` | token 不正确 | 检查 Bearer token |
| `pool_exhausted` | 并发超限 | 调大 `max_processes` |
| claude 卡住 | permission 请求未回复 | 已自动处理（auto-allow） |
| Discord 推送失败 | `account_id` 错误或缺失 | 确认用 `default`，不是 agent name |
| Discord 500 | target 格式错误 | DM 用 `user:<id>`，频道用 `channel:<id>` |
| job 卡住 | agent 进程异常 | 10min 后自动标记 failed |
