# ACP Bridge

将本地 CLI agent（如 Kiro CLI、Claude Code）通过 [ACP 协议](https://agentcommunicationprotocol.dev/) HTTP API 对外暴露的桥接服务。

## 架构概览

```
┌──────────────┐    HTTP/JSON     ┌──────────────┐    subprocess    ┌──────────────┐
│  远程调用方   │ ──────────────▶ │  ACP Bridge   │ ──────────────▶ │  CLI Agent    │
│  (agent/脚本) │ ◀────────────── │  (uvicorn)    │ ◀────────────── │  (本地进程)   │
└──────────────┘   ACP response   └──────────────┘    stdout parse  └──────────────┘
                                        │
                                   IP 白名单 +
                                   Bearer Token
```

核心思路：Bridge 是一个 ASGI 应用，接收 ACP 协议的 HTTP 请求，将 prompt 通过 subprocess 传给本地 CLI，按 `output_mode` 解析终端输出后返回标准 ACP 响应。

## 功能

- 启动时自动注册所有 `enabled: true` 的 agent，通过配置文件管理
- Bearer Token 认证 + IP 白名单双重安全
- 基于 UUID session_id 的会话隔离，不同 agent 互不干扰
- 支持多轮对话（同一 session_id 自动 resume，可按 agent 关闭）
- 按 `output_mode` 配置不同的输出解析策略（`kiro`、`raw` 等）
- 自动清理过期 session（`session_ttl_hours` 可配置）
- `--verbose` 模式输出详细请求/响应日志

## 项目结构

```
acp-bridge/
├── main.py           # 入口：读取配置、自动注册 enabled agents、session 清理、启动 uvicorn
├── src/
│   ├── agents.py     # CLI 调用逻辑：subprocess 管理、按 output_mode 解析输出
│   └── security.py   # 安全中间件（IP 白名单 + Bearer Token 认证）
├── skill/
│   ├── SKILL.md      # OpenClaw skill 定义
│   └── acp-client.sh # 客户端调用脚本
├── test/
│   └── test.sh       # 集成测试脚本
├── config.yaml       # 服务配置（端口、安全、agent 定义、session TTL）
├── pyproject.toml    # Python 项目依赖
└── uv.lock           # 锁定依赖版本
```

## 环境要求

- Python >= 3.12
- [uv](https://docs.astral.sh/uv/) 包管理器
- 已安装并登录的 CLI agent（如 `kiro-cli`、`claude`）

## 搭建步骤

### 1. 初始化项目

```bash
mkdir acp-bridge && cd acp-bridge
uv init
uv add acp-sdk pyyaml
```

依赖说明：
- `acp-sdk` — IBM 开源的 ACP 协议 SDK，提供 Server/Agent 抽象和 ASGI app 生成
- `pyyaml` — 解析 config.yaml

### 2. 创建配置文件

`config.yaml`：

```yaml
server:
  host: "0.0.0.0"
  port: 8001
  session_ttl_hours: 24          # 超过此时长的 session 自动清理

security:
  auth_token: "<your-token>"     # Bearer 认证 token
  allowed_ips:
    - "127.0.0.1"
    - "<server-private-ip>"
    # - "<client-ip>"

agents:
  kiro:
    enabled: true                # 启动时自动注册
    command: "kiro-cli"
    args: ["chat", "--no-interactive", "--trust-all-tools", "--wrap", "never"]
    resume_flag: "--resume"
    supports_resume: true        # 支持多轮对话 resume
    output_mode: "kiro"          # 使用 kiro 格式解析器
    description: "Kiro CLI agent"
    session_base: "/tmp/acp-bridge-sessions"
  claude:
    enabled: true
    command: "claude"
    args: ["-p", "--output-format", "text"]
    resume_flag: "--resume"
    supports_resume: false       # claude 不支持 resume
    output_mode: "raw"           # 纯文本，不做格式解析
    description: "Claude Code agent"
    session_base: "/tmp/acp-bridge-sessions"
```

关键配置说明：
- `enabled`：控制 agent 是否在启动时注册，设为 `false` 即禁用
- `session_ttl_hours`：session 目录超过此时长自动清理（每小时检查一次）
- `output_mode`：输出解析策略，`kiro` 解析 `> `/`│` 格式，`raw` 直接返回纯文本
- `supports_resume`：是否启用多轮对话的 `--resume` 机制，不支持的 CLI 设为 `false`
- `auth_token`：Bearer Token 认证，客户端需在 header 中携带

### 3. 启动服务

```bash
# 前台运行（开发调试）
uv run main.py

# 详细日志模式
uv run main.py --verbose

# 后台运行（生产）
nohup uv run main.py > nohup.out 2>&1 &

# 自定义端口
uv run main.py --port 9000
```

### 4. 验证

```bash
# 检查服务
curl -H "Authorization: Bearer <token>" http://localhost:8001/agents

# 客户端调用
export ACP_BRIDGE_URL=http://localhost:8001
export ACP_TOKEN=<token>
./skill/acp-client.sh -l
./skill/acp-client.sh "hello"
./skill/acp-client.sh -a claude "hello"

# 运行集成测试
bash test/test.sh
```

## 客户端调用

### 使用 acp-client.sh（推荐）

```bash
export ACP_BRIDGE_URL=http://<bridge-ip>:8001
export ACP_TOKEN=<your-token>

# 列出可用 agents
./skill/acp-client.sh -l

# 调用 kiro（自动生成 session_id）
./skill/acp-client.sh "帮我看看项目结构"

# 指定 agent
./skill/acp-client.sh -a claude "分析这段代码"

# 指定 session_id 继续对话（必须 UUID 格式）
./skill/acp-client.sh -s 00000000-0000-0000-0000-000000000001 "继续上面的问题"
```

### 使用 curl

```bash
# 查看可用 agents
curl -H "Authorization: Bearer <token>" http://<bridge-ip>:8001/agents

# 调用 agent
curl -X POST http://<bridge-ip>:8001/runs \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer <token>" \
  -d '{
    "agent_name": "kiro",
    "session_id": "00000000-0000-0000-0000-000000000001",
    "input": [{"role":"user","parts":[{"content":"hello","content_type":"text/plain"}]}]
  }'
```

## API 参考

### GET /agents

返回已注册的 agent 列表。

```json
{
  "agents": [
    {"name": "kiro", "description": "Kiro CLI agent"},
    {"name": "claude", "description": "Claude Code agent"}
  ]
}
```

### POST /runs

调用 agent 执行任务。

请求：
```json
{
  "agent_name": "kiro",
  "session_id": "可选 UUID，多轮对话时传入",
  "input": [
    {
      "role": "user",
      "parts": [{"content": "你的问题", "content_type": "text/plain"}]
    }
  ]
}
```

成功响应：
```json
{
  "run_id": "<uuid>",
  "status": "completed",
  "session_id": "<uuid>",
  "output": [
    {
      "role": "agent/kiro",
      "parts": [{"content": "回复内容", "content_type": "text/plain"}]
    }
  ]
}
```

## Session 管理

- `session_id` 必须是 **UUID 格式**，非 UUID 会返回 422 错误
- 客户端必须自行保存 session_id，变化即视为新对话
- 不同 agent 使用不同 session_id，避免会话冲突
- `acp-client.sh` 不指定 `-s` 时按 agent 名自动生成确定性 UUID
- 超过 `session_ttl_hours` 的 session 目录自动清理

## 扩展：添加新的 CLI Agent

1. 在 `config.yaml` 中添加配置块：

```yaml
agents:
  mycli:
    enabled: true
    command: "mycli"
    args: ["--non-interactive"]
    supports_resume: false
    output_mode: "raw"           # 或注册自定义解析器
    description: "My custom CLI agent"
    session_base: "/tmp/acp-bridge-sessions"
```

2. 如果输出格式特殊，在 `src/agents.py` 的 `REPLY_EXTRACTORS` 中注册新的解析函数：

```python
def extract_reply_mycli(raw: str) -> str:
    # 自定义解析逻辑
    return strip_ansi(raw).strip()

REPLY_EXTRACTORS = {
    "kiro": extract_reply_kiro,
    "raw": extract_reply_raw,
    "mycli": extract_reply_mycli,
}
```

3. 重启服务即可生效。

## OpenClaw Skill 集成

`skill/` 目录包含符合 [AgentSkills 规范](https://agentskills.io/specification) 的 skill 定义，可让 OpenClaw agent 直接调用远程 CLI agent。

安装方式（任选其一）：

```bash
# 全局安装
cp -r skill/ ~/.openclaw/skills/acp-bridge/

# 或工作区安装（优先级更高）
cp -r skill/ <workspace>/skills/acp-bridge/
```

安装后 OpenClaw 会在启动时自动加载该 skill。详细用法见 [skill/SKILL.md](skill/SKILL.md)。

## 安全注意事项

- IP 白名单 + Bearer Token 双重认证
- CLI agent 以 Bridge 进程的用户权限运行，注意权限最小化
- `auth_token` 建议通过环境变量注入，避免明文存储在配置文件中
- 生产环境建议配合安全组/防火墙限制端口访问

## 故障排查

| 现象 | 原因 | 处理 |
|------|------|------|
| `403 forbidden` | IP 不在白名单 | 在 `config.yaml` 的 `allowed_ips` 中添加客户端 IP |
| `401 unauthorized` | token 不正确 | 检查 `Authorization: Bearer <token>` 是否匹配 |
| `422 Unprocessable` | session_id 不是 UUID | 使用 UUID 格式 |
| 请求一直不返回 | CLI 子进程卡住或 session 冲突 | 用 `--verbose` 查看日志 |
| agent 返回空 | CLI 在无 TTY 环境行为异常 | 检查 `output_mode` 配置，查看服务端日志 |
| `连接失败` | 服务未启动或端口不对 | 确认 Bridge 进程在运行 |

## 已知限制

- 同步调用模型：每次请求阻塞等待 CLI 执行完成（`raw` 模式支持流式输出）
- 无内置重试/超时机制，长时间运行的任务可能导致 HTTP 超时
- 后台运行时需注意 CLI 的 TTY 检测行为（已通过环境变量 `TERM=dumb` 等缓解）
