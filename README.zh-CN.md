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
║    🤖 Claude ──┼──► acp 🌉 ──► 🦞 OpenClaw ──► 🌍 world     ║
║    🤖 Codex ──┘                                              ║
║                                                              ║
║          https://github.com/xiwan/acp-bridge                 ║
╚══════════════════════════════════════════════════════════════╝

        ~ Local AI agents 🔌 ACP protocol 🦞 The world ~
```

# ACP Bridge

[English](README.md)

将本地 CLI agent（如 Kiro CLI、Claude Code、[OpenAI Codex](https://github.com/openai/codex)）通过 [ACP 协议](https://agentclientprotocol.com/) HTTP API 对外暴露的桥接服务，支持异步任务和 Discord 推送。

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
- OpenClaw tools 代理：统一入口调用 message/tts/nodes/cron/web_search 等
- 客户端纯 bash + jq，零 python 依赖

## 版本记录

| 版本 | 日期 | 说明 |
|------|------|------|
| v0.8.0 | 2026-03-19 | Docker light 模式：仅网关镜像，agent 从宿主机挂载 |
| v0.7.3 | 2026-03-18 | 请求级 cwd、tools 代理修复、测试改进 |
| v0.7.2 | 2026-03-18 | 多 IM 格式化（Discord/飞书）、统一 target 字段、systemd |
| v0.7.1 | 2026-03-18 | ACP agent 合规测试、AGENT_SPEC.md、echo-agent 参考实现 |
| v0.7.0 | 2026-03-17 | OpenClaw tools 代理、agent 健康探测、自动恢复 |
| v0.6.0 | 2026-03-15 | Codex PTY 支持、LiteLLM 集成、acp-client.sh |

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
│   └── acp-client.sh    # Agent 客户端脚本（bash + jq）
├── tools/
│   └── tools-client.sh  # OpenClaw tools 客户端（调试 + 集成）
├── examples/
│   └── echo-agent.py    # 最小 ACP 兼容参考 agent
├── docker/
│   └── light/           # 轻量 Docker 镜像（仅网关，agent 从宿主机挂载）
├── test/
│   ├── lib.sh                     # 测试公共库（断言函数、环境初始化）
│   ├── test.sh                    # 全量测试入口
│   ├── test_agent_compliance.sh   # Agent 合规测试（直接 stdio，无需 Bridge）
│   ├── test_common.sh             # 公共测试（agent 列表、错误处理）
│   ├── test_tools.sh              # OpenClaw tools 代理测试
│   ├── test_kiro.sh               # Kiro agent 测试
│   ├── test_claude.sh             # Claude agent 测试
│   ├── test_codex.sh              # Codex agent 测试
│   ├── test_docker.sh             # Docker 镜像测试
│   └── reports/                   # 测试报告
├── AGENT_SPEC.md        # ACP agent 集成规范
├── config.yaml          # 服务配置
├── pyproject.toml
└── uv.lock
```

## 环境要求

- Python >= 3.12（服务端）
- [uv](https://docs.astral.sh/uv/) 包管理器
- 已安装的 CLI agent（如 `kiro-cli`、`claude-agent-acp`、`codex`）
- 客户端依赖：`curl`、`jq`、`uuidgen`
- Codex 需要：[Node.js](https://nodejs.org/)（npm）、[LiteLLM](https://github.com/BerriAI/litellm)（非 OpenAI 模型需代理）

## 快速开始

```bash
cd acp-bridge
cp config.yaml.example config.yaml
# 编辑 config.yaml
uv sync
uv run main.py
```

## Docker 快速开始

轻量 Docker 镜像，仅包含 ACP Bridge 网关。Agent CLI（Kiro、Claude Code、Codex）保留在宿主机上，通过 volume 挂载到容器中。

```bash
# 1. 准备配置
cp config.yaml.example config.yaml
# 编辑 config.yaml

# 2. 设置环境变量
export ACP_BRIDGE_TOKEN=<your-token>

# 3. 编辑 docker/light/docker-compose.yml
#    取消注释你已安装的 agent 对应的 volume 挂载

# 4. 构建并运行
docker compose -f docker/light/docker-compose.yml up -d

# 查看日志
docker compose -f docker/light/docker-compose.yml logs -f
```

详见 `docker/light/docker-compose.yml` 中各 agent 的挂载示例。

## Codex + LiteLLM 配置

[OpenAI Codex CLI](https://github.com/openai/codex) 不原生支持 ACP 协议，因此使用 PTY 模式（子进程）接入。要使用非 OpenAI 模型（如 Bedrock 上的 Kimi K2.5），需要 [LiteLLM](https://github.com/BerriAI/litellm) 作为 OpenAI 兼容代理。

### 安装

```bash
# Codex CLI
npm i -g @openai/codex

# LiteLLM 代理
pip install 'litellm[proxy]'
```

### 配置 Codex

```toml
# ~/.codex/config.toml
model = "bedrock/moonshotai.kimi-k2.5"
model_provider = "bedrock"

[model_providers.bedrock]
name = "AWS Bedrock via LiteLLM"
base_url = "http://localhost:4000/v1"
env_key = "LITELLM_API_KEY"
```

### 配置 LiteLLM

```yaml
# ~/.codex/litellm-config.yaml
model_list:
  - model_name: "bedrock/moonshotai.kimi-k2.5"
    litellm_params:
      model: "bedrock/moonshotai.kimi-k2.5"
      aws_region_name: "us-east-1"

general_settings:
  master_key: "sk-litellm-bedrock"

litellm_settings:
  drop_params: true
```

`drop_params: true` 是必须的 — Codex 会发送 Bedrock 不支持的参数（如 `web_search_options`）。

LiteLLM 使用 EC2 实例的 AWS 凭证（IAM Role 或 `~/.aws/credentials`）访问 Bedrock。`master_key` 只是代理自身的认证 token。

### 启动 LiteLLM

```bash
LITELLM_API_KEY="sk-litellm-bedrock" litellm --config ~/.codex/litellm-config.yaml --port 4000
```

### 数据流

```
acp-bridge ──(PTY)──► codex exec ──(HTTP)──► LiteLLM :4000 ──(Bedrock API)──► Kimi K2.5
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
  account_id: "default"
  discord_target: "channel:<default-channel-id>"

security:
  auth_token: "${ACP_BRIDGE_TOKEN}"
  allowed_ips:
    - "127.0.0.1"

litellm:
  url: "http://localhost:4000"
  required_by: ["codex"]
  env:
    LITELLM_API_KEY: "${LITELLM_API_KEY}"

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
  codex:
    enabled: true
    mode: "pty"
    command: "codex"
    args: ["exec", "--full-auto", "--skip-git-repo-check"]
    working_dir: "/tmp"
    description: "OpenAI Codex CLI agent"
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

![异步任务示例](statics/sample-aysnc-job.png)

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
| GET | `/tools` | 列出可用 OpenClaw tools | 需要 |
| POST | `/tools/invoke` | 调用 OpenClaw tool（代理） | 需要 |
| GET | `/health` | 健康检查 | 不需要 |
| GET | `/health/agents` | agent 状态 | 需要 |
| DELETE | `/sessions/{agent}/{session_id}` | 关闭 session | 需要 |

## OpenClaw Tools 代理

ACP Bridge 代理 OpenClaw 的 tool 系统，提供统一入口同时调用 agent 和 tool。

### 可用 Tools

| Tool | 说明 |
|------|------|
| `message` | 跨渠道发消息（Discord/Telegram/Slack/WhatsApp/Signal/iMessage） |
| `tts` | 文字转语音 |
| `web_search` | 搜索网页 |
| `web_fetch` | 抓取网页内容 |
| `nodes` | 控制配对设备（通知、执行命令、摄像头） |
| `cron` | 管理定时任务 |
| `gateway` | Gateway 配置和重启 |
| `image` | AI 图片分析 |
| `browser` | 控制浏览器（打开、截图、导航） |

### 客户端调用

```bash
# 列出可用 tools
./tools/tools-client.sh -l

# 发送 Discord 消息
./tools/tools-client.sh message send \
  --arg channel=discord \
  --arg target="channel:123456" \
  --arg message="Hello from ACP Bridge"

# 文字转语音
./tools/tools-client.sh tts "今天构建通过了"

# 搜索
./tools/tools-client.sh web_search "Python 3.13 新特性"

# 给 Mac 发通知
./tools/tools-client.sh nodes notify \
  --arg node="office-mac" \
  --arg title="部署完成" \
  --arg body="v1.2.3 已上线"
```

### 直接 API 调用

```bash
curl -X POST http://<bridge>:8001/tools/invoke \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{
    "tool": "message",
    "action": "send",
    "args": {
      "channel": "discord",
      "target": "channel:123456",
      "message": "Hello from ACP Bridge"
    }
  }'
```

需要在 `config.yaml` 中配置 `webhook.url` 指向 OpenClaw Gateway。

## 测试

### Agent 合规测试

验证 CLI agent 是否正确实现 ACP 协议 — **无需启动 Bridge**：

```bash
bash test/test_agent_compliance.sh kiro-cli acp --trust-all-tools
bash test/test_agent_compliance.sh claude-agent-acp
bash test/test_agent_compliance.sh python3 examples/echo-agent.py
```

覆盖：initialize、session/new、session/prompt（通知 + 结果）、ping。详见 [AGENT_SPEC.md](AGENT_SPEC.md)。

### 集成测试

```bash
ACP_TOKEN=<token> bash test/test.sh http://127.0.0.1:8001
```

按 agent 单独测试：

```bash
ACP_TOKEN=<token> bash test/test_codex.sh
ACP_TOKEN=<token> bash test/test_kiro.sh
ACP_TOKEN=<token> bash test/test_claude.sh
```

从主入口过滤：

```bash
ACP_TOKEN=<token> bash test/test.sh http://127.0.0.1:8001 --only codex
```

覆盖：agents 列表、同步/流式调用、多轮对话、Claude、Codex、异步任务、错误处理。

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
| Codex: 不信任目录 | `/tmp` 不是 git repo | 添加 `--skip-git-repo-check` 到 args |
| Codex: 缺少 LITELLM_API_KEY | 环境变量未传递 | 在 config 中设置 `litellm.env.LITELLM_API_KEY` |
| Codex: 不支持的参数 | Bedrock 拒绝 Codex 参数 | LiteLLM 配置 `drop_params: true` |

## 安全问题

详见 [CONTRIBUTING](CONTRIBUTING.md#security-issue-notifications)。

## 许可证

本项目基于 MIT-0 许可证。详见 [LICENSE](LICENSE) 文件。
