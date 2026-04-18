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
║   🦞 OpenClaw ─┐              ┌──► 🤖 Kiro / Claude / Codex  ║
║                 ┼──► acp 🌉 ──┼──► 🤖 Qwen / OpenCode       ║
║   🌐 Web UI ──┘              └──► 🏭 Harness / ...          ║
║                                                              ║
║          https://github.com/xiwan/acp-bridge                              ║
╚══════════════════════════════════════════════════════════════╝

        ~ 🌐 The world 🔌 ACP protocol 🤖 Local AI agents ~
```

# ACP Bridge

[![GitHub stars](https://img.shields.io/github/stars/xiwan/acp-bridge?style=social)](https://github.com/xiwan/acp-bridge/stargazers)
[![GitHub forks](https://img.shields.io/github/forks/xiwan/acp-bridge?style=social)](https://github.com/xiwan/acp-bridge/network/members)
[![GitHub Discussions](https://img.shields.io/github/discussions/xiwan/acp-bridge?logo=github&label=Discussions)](https://github.com/xiwan/acp-bridge/discussions)
[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)

[![Agent Guide](https://img.shields.io/badge/Agent_Guide-for_AI_Agents-blue?logo=robot)](AGENT.md)
[![AWS Blog](https://img.shields.io/badge/AWS_Blog-Published-orange?logo=amazonaws)](https://aws.amazon.com/cn/blogs/china/enable-kiro-and-claude-code-for-im-with-acp-bridge-async-ai-workflow/)

A bridge service that exposes local CLI agents (Kiro CLI, Claude Code, [OpenAI Codex](https://github.com/openai/codex), etc.) via [ACP (Agent Client Protocol)](https://agentclientprotocol.com/) over HTTP, with async job support and Discord push notifications.

## Install

```bash
curl -fsSL https://raw.githubusercontent.com/xiwan/acp-bridge/main/install.sh | bash
```

Interactive installer: auto-detects agent CLIs, configures tokens, generates `config.yaml`, and starts the server. On completion, the installer prints the **OpenClaw skill setup info** you need to connect your IM bot:

```
┌──────────────────────────────────────────────────────────────┐
│  🦞 OpenClaw Skill Setup                                     │
│                                                              │
│  Skill URL:                                                  │
│    https://github.com/xiwan/acp-bridge/tree/main/skill       │
│                                                              │
│  Then set these env vars in OpenClaw:                        │
│    ACP_TOKEN=<your-token>                                    │
│    ACP_BRIDGE_URL=http://<your-ip>:18010                     │
└──────────────────────────────────────────────────────────────┘
```

Tell your OpenClaw bot to install the skill at the URL above, then set `ACP_TOKEN` and `ACP_BRIDGE_URL` so it can reach the Bridge. See [Quick Start](#quick-start) for manual setup.

## When to Use ACP Bridge

> You have powerful CLI agents on a dev machine. You want the rest of your team — or your bots — to use them too.

| Scenario | How ACP Bridge Helps |
|----------|---------------------|
| **Team AI gateway** | One EC2 runs Kiro + Claude + Codex; everyone calls them via HTTP — no local install needed |
| **IM-driven development** | Send a Discord/Feishu message → agent executes → result pushed back to chat |
| **Async code tasks** | Submit a refactor or review job, go grab coffee, get notified when it's done |
| **Multi-agent orchestration** | Chain agents in sequence, race them in parallel, or let them debate in conversation mode |
| **Prompt-as-a-service** | Define reusable prompt templates; non-technical users pick a template and fill in variables |
| **Agent marketplace** | Same `harness-factory` binary + different profiles = code reviewer, DevOps helper, translator — all behind one API |

## Architecture

```
┌──────────┐  HTTP JSON req     ┌──────────────┐  ACP stdio     ┌──────────────┐
│ OpenClaw │──────────────────▶│  ACP Bridge  │──────────────▶│  CLI Agent   │
│ Gateway  │◀──── SSE stream ───│  (uvicorn)   │◀── JSON-RPC ──│  kiro/claude │
└──────────┘◀── /tools/invoke ──└──────────────┘               └──────────────┘
```

## Features

- Native ACP protocol support: structured event stream (thinking / tool_call / text / status)
- Process pool: reuse subprocess per session, automatic multi-turn context retention
- Memory protection: auto-evict idle connections when system memory exceeds threshold (OOM prevention)
- Sync + SSE streaming + Markdown card output
- Async jobs: submit and return immediately, webhook callback on completion
- Discord push: send results via OpenClaw Gateway `/tools/invoke`
- Job monitoring: stuck detection (>10min auto-fail), webhook retry, status stats
- Auto-reply to `session/request_permission` (prevents Claude from hanging)
- Bearer Token + IP allowlist dual authentication
- OpenClaw tools proxy: unified entry point for message/tts/nodes/cron/web_search and more
- Web UI (opt-in): chat interface at `/ui` with persistence (SQLite), message folding, and settings panel
- Client is pure bash + jq, zero Python dependency
- Harness Factory support: profile-driven lightweight agents via [harness-factory](https://github.com/xiwan/harness-factory) — same binary, different profiles, different agents

## Agent Compatibility Matrix

> Which CLI agents work with ACP Bridge today?

| Agent | Vendor | ACP | Mode | Status | Tests |
|-------|--------|-----|------|--------|-------|
| [Kiro CLI](https://github.com/aws/kiro-cli) | AWS | ✅ Native | `acp` | ✅ Integrated | 7/7 |
| [Claude Code](https://github.com/anthropics/claude-code) | Anthropic | ✅ Native | `acp` | ✅ Integrated | 5/5 |
| [Qwen Code](https://www.npmjs.com/package/@anthropic-ai/qwen-code) | Alibaba | ✅ `--acp` | `acp` | ✅ Integrated | 6/6 |
| [OpenAI Codex](https://github.com/openai/codex) | OpenAI | ❌ | `pty` | ✅ Integrated | 6/6 |
| [Gemini CLI](https://github.com/google-gemini/gemini-cli) | Google | 🧪 `--experimental-acp` | — | 🟡 Planned | — |
| [Copilot CLI](https://docs.github.com/en/copilot/reference/acp-server) | GitHub | ✅ `--acp` | — | 🟡 Planned | — |
| [OpenCode](https://github.com/opencode-ai/opencode) | Open Source | ✅ `opencode acp` | `acp` | ✅ Integrated | 6/6 |
| [Harness Factory](https://github.com/xiwan/harness-factory) | Open Source | ✅ Native | `acp` | ✅ Integrated | 4/4 |
| [CoStrict](https://github.com/zgsm-ai/costrict) | Open Source 🇨🇳 | ✅ Native | — | 🟡 Planned | — |
| [Trae Agent](https://github.com/bytedance/trae-agent) | ByteDance 🇨🇳 | ❌ | — | ⚪ No ACP | — |
| [Aider](https://github.com/Aider-AI/aider) | Open Source | ❌ | — | ⚪ No ACP | — |

**Legend:** ✅ Integrated — 🟡 Planned (ACP-ready) — ⚪ No ACP support yet — 🧪 Experimental

> Agents without ACP can still be integrated via PTY mode (like Codex). PRs welcome!

## Changelog

See [CHANGELOG.md](CHANGELOG.md) for full version history. Current: v0.15.3

## Project Structure

```
acp-bridge/
├── main.py              # Entry: process pool, handler registration, job/health endpoints
├── install.sh           # Interactive one-line installer (agent detection, token setup, config generation)
├── start.sh             # Quick start: loads .env, starts LiteLLM + Bridge
├── bridge-ctl.sh        # Lifecycle control: status/restart/stop/logs/health (systemd)
├── src/
│   ├── acp_client.py    # ACP process pool + JSON-RPC connection management
│   ├── agents.py        # Agent handlers (ACP mode + PTY fallback)
│   ├── auto_detect.py   # Zero-config: scan PATH for agent CLIs, generate config
│   ├── jobs.py          # Async job manager (submit, monitor, webhook callback)
│   ├── pipeline.py      # Multi-agent pipeline (sequence, parallel, race, random, conversation) + shared workspace
│   ├── sse.py           # ACP session/update → SSE event conversion
│   └── security.py      # Security middleware (IP allowlist + Bearer Token)
├── skill/
│   ├── SKILL.md         # Kiro/OpenClaw skill definition
│   └── acp-client.sh    # Agent client script (bash + jq)
├── tools/
│   └── tools-client.sh  # OpenClaw tools client (debug + integration)
├── examples/
│   └── echo-agent.py    # Minimal ACP-compliant reference agent
├── test/
│   ├── lib.sh                     # Test helpers (assertions, env init)
│   ├── test.sh                    # Full test suite runner
│   ├── test_agent_compliance.sh   # Agent compliance test (direct stdio, no Bridge needed)
│   ├── test_common.sh             # Common tests (agent listing, error handling)
│   ├── test_tools.sh              # OpenClaw tools proxy tests
│   ├── test_kiro.sh               # Kiro agent tests
│   ├── test_claude.sh             # Claude agent tests
│   ├── test_codex.sh              # Codex agent tests
│   ├── test_qwen.sh               # Qwen agent tests
│   ├── test_opencode.sh           # OpenCode agent tests
│   └── reports/                   # Test reports
├── AGENT_SPEC.md        # ACP agent integration specification
├── config.yaml          # Service configuration (auto-generated or manual)
├── pyproject.toml
└── uv.lock
```

## Prerequisites

- Python >= 3.12
- [uv](https://docs.astral.sh/uv/) package manager
- A CLI agent installed (e.g. `kiro-cli`, `claude-agent-acp`, `codex`)
- Client dependencies: `curl`, `jq`, `uuidgen`
- For Codex: [Node.js](https://nodejs.org/) (npm), [LiteLLM](https://github.com/BerriAI/litellm) (if using non-OpenAI models via proxy)

## Quick Start

### One-line install

```bash
curl -fsSL https://raw.githubusercontent.com/xiwan/acp-bridge/main/install.sh | bash
```

### Zero-config (auto-detect agents in PATH)

```bash
cd acp-bridge
uv sync
uv run main.py
# No config.yaml needed — auto-detects installed agent CLIs
# Prints auth token on startup; set ACP_BRIDGE_TOKEN env for a fixed token
```

### With config file

```bash
cd acp-bridge
cp config.yaml.example config.yaml
# Edit config.yaml with your settings
uv sync
uv run main.py
```

## Docker Quick Start

A lightweight Docker image containing only the ACP Bridge gateway. Agent CLIs (Kiro, Claude Code, Codex) stay on your host — mount them into the container as needed.

```bash
# 1. Prepare config
cp config.yaml.example config.yaml
# Edit config.yaml with your settings

# 2. Edit docker/light/docker-compose.yml
#    Uncomment volume mounts for the agents you have installed

# 3. Set environment variables (pick one method)

# Method A: .env file (recommended, works with sudo)
cp docker/light/.env.example docker/light/.env
# Edit docker/light/.env with your tokens

# Method B: inline env vars
sudo \
  ACP_BRIDGE_TOKEN=<your-token> \
  CLAUDE_CODE_USE_BEDROCK=1 \
  ANTHROPIC_MODEL=<your-model> \
  LITELLM_API_KEY=<your-litellm-key> \
  docker compose -f docker/light/docker-compose.yml up -d

# 4. Build and run
sudo docker compose -f docker/light/docker-compose.yml up -d --build

# Check logs
sudo docker compose -f docker/light/docker-compose.yml logs -f
```

> **Note:** When using `sudo`, shell environment variables and `~` paths are NOT passed to Docker. Use a `.env` file or pass variables inline as shown above.

See `docker/light/docker-compose.yml` for mount examples for each agent.

## Codex + LiteLLM Setup

[OpenAI Codex CLI](https://github.com/openai/codex) doesn't support ACP protocol natively, so it runs in PTY mode (subprocess). To use non-OpenAI models (e.g. Kimi K2.5 on Bedrock), Codex needs [LiteLLM](https://github.com/BerriAI/litellm) as an OpenAI-compatible proxy.

### Install

```bash
# Codex CLI
npm i -g @openai/codex

# LiteLLM proxy
pip install 'litellm[proxy]'
```

### Configure Codex

```toml
# ~/.codex/config.toml
model = "bedrock/moonshotai.kimi-k2.5"
model_provider = "bedrock"

[model_providers.bedrock]
name = "AWS Bedrock via LiteLLM"
base_url = "http://localhost:4000/v1"
env_key = "LITELLM_API_KEY"
```

### Configure LiteLLM

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

`drop_params: true` is required — Codex sends parameters (e.g. `web_search_options`) that Bedrock doesn't support.

LiteLLM uses the EC2 instance's AWS credentials (IAM Role or `~/.aws/credentials`) to access Bedrock. The `master_key` is just the proxy's own auth token.

### Start LiteLLM

```bash
LITELLM_API_KEY="sk-litellm-bedrock" litellm --config ~/.codex/litellm-config.yaml --port 4000
```

### Data Flow

```
acp-bridge ──(PTY)──► codex exec ──(HTTP)──► LiteLLM :4000 ──(Bedrock API)──► Kimi K2.5
```

## Configuration

```yaml
server:
  host: "0.0.0.0"
  port: 18010
  session_ttl_hours: 24
  shutdown_timeout: 30
  ui: false                                     # enable Web UI at /ui (or use --ui flag)
  upload_dir: "/tmp/acp-uploads"                # file upload storage directory

pool:
  max_processes: 8
  max_per_agent: 4
  memory_limit_percent: 80

webhook:
  url: "http://<openclaw-ip>:18789/tools/invoke"
  token: "${OPENCLAW_TOKEN}"
  account_id: "default"
  target: "channel:<default-channel-id>"        # also accepts feishu targets

security:
  auth_token: "${ACP_BRIDGE_TOKEN}"
  allowed_ips:
    - "127.0.0.1"

litellm:
  url: "http://localhost:4000"
  required_by: ["codex", "qwen"]
  env:
    LITELLM_API_KEY: "${LITELLM_API_KEY}"

harness:
  binary: ""                                    # absolute path to harness-factory; empty = use PATH

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
  qwen:
    enabled: true
    mode: "acp"
    command: "qwen"
    acp_args: ["--acp"]
    working_dir: "/tmp"
    description: "Qwen Code agent"
  opencode:
    enabled: true
    mode: "acp"
    command: "opencode"
    acp_args: ["acp"]
    working_dir: "/tmp"
    description: "OpenCode agent (open source, multi-provider)"
  # harness-factory: same binary, different profiles → different agents
  # name is arbitrary — use "harness", "pr-reviewer", "translator", etc.
  harness:
    enabled: true
    mode: "acp"
    command: "harness-factory"
    acp_args: []
    working_dir: "/tmp"
    description: "Harness Factory lite agent (profile-driven)"
    profile:
      tools:
        fs: { permissions: [read, list] }
        git: { permissions: [diff, log, show] }
        shell: { allowlist: [pytest, mypy, grep] }
      orchestration: free
      resources:
        timeout: 300s
        max_turns: 20
      agent:
        model: "auto"
        system_prompt: "You are a code reviewer."
        temperature: 0.3
```

## Client Usage

### acp-client.sh

```bash
export ACP_BRIDGE_URL=http://<bridge-ip>:18010
export ACP_TOKEN=<your-token>

# List available agents
./skill/scripts/acp-client.sh -l

# Sync call
./skill/scripts/acp-client.sh "Explain the project structure"

# Streaming call
./skill/scripts/acp-client.sh --stream "Analyze this code"

# Markdown card output (ideal for IM display)
./skill/scripts/acp-client.sh --card -a kiro "Introduce yourself"

# Specify agent
./skill/scripts/acp-client.sh -a claude "hello"

# Upload a file
./skill/scripts/acp-client.sh --upload data.csv

# Multi-turn conversation
./skill/scripts/acp-client.sh -s 00000000-0000-0000-0000-000000000001 "continue"
```

## Async Jobs + Discord Push

Submit long-running tasks and get results pushed to Discord automatically.

![Async Job Sample](statics/sample-aysnc-job.png)

### Submit

```bash
curl -X POST http://<bridge>:18010/jobs \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{
    "agent_name": "kiro",
    "prompt": "Refactor the module",
    "target": "user:<user-id>",
    "channel": "discord",
    "callback_meta": {"account_id": "default"}
  }'
# → {"job_id": "xxx", "status": "pending"}
```

#### Feishu Example

```bash
curl -X POST http://<bridge>:18010/jobs \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{
    "agent_name": "kiro",
    "prompt": "Analyze the codebase",
    "target": "user:<feishu-open-id>",
    "channel": "feishu",
    "callback_meta": {"account_id": "main"}
  }'
```

### Query

```bash
curl http://<bridge>:18010/jobs/<job_id> \
  -H "Authorization: Bearer <token>"
```

### Callback Flow

```
POST /jobs → Bridge executes in background → On completion POST to OpenClaw /tools/invoke
  → OpenClaw sends to Discord/Feishu/... via message tool → User receives result
```

> **Note:** Async job push currently requires [OpenClaw](https://github.com/NousResearch/hermes-agent) (formerly OpenClaw Gateway) or a direct Discord webhook as the callback target. [Hermes Agent](https://github.com/NousResearch/hermes-agent) does not yet expose a "send message" HTTP API — its webhook endpoint (`/webhooks/{route}`) triggers a full agent run rather than relaying messages directly. Hermes support is planned for a future release.

### target Format

| Scenario | Format | Example |
|----------|--------|---------|
| Discord channel | `channel:<id>` or `#name` | `channel:1477514611317145732` |
| Discord DM | `user:<user_id>` | `user:123456789` |
| Feishu user | `user:<open_id>` | `user:ou_2dfd02ef...` |
| Feishu group | `<chat_id>` | `oc_xxx` |

`account_id` refers to the OpenClaw bot account — `default` for Discord, `main` for Feishu (depends on your OpenClaw config).

### Job Monitoring

- `GET /jobs` — List all jobs + status stats
- Patrol every 60s: jobs stuck >10min are auto-marked as failed + notified
- Failed webhook sends are retried automatically until success or job expiry

## API Endpoints

| Method | Path | Description | Auth |
|--------|------|-------------|------|
| GET | `/agents` | List registered agents | Yes |
| POST | `/runs` | Sync/streaming agent call | Yes |
| POST | `/jobs` | Submit async job | Yes |
| GET | `/jobs` | List all jobs + stats | Yes |
| GET | `/jobs/{job_id}` | Query single job | Yes |
| POST | `/pipelines` | Submit multi-agent pipeline | Yes |
| GET | `/pipelines` | List all pipelines | Yes |
| GET | `/pipelines/{id}` | Query single pipeline | Yes |
| GET | `/tools` | List available OpenClaw tools | Yes |
| POST | `/tools/invoke` | Invoke an OpenClaw tool (proxy) | Yes |
| POST | `/chat/messages` | Save a chat message (Web UI) | Yes |
| GET | `/chat/messages` | Load recent chat messages (Web UI) | Yes |
| DELETE | `/chat/messages` | Clear all chat messages (Web UI) | Yes |
| POST | `/chat/fold` | Fold a session's messages (Web UI) | Yes |
| POST | `/files` | Upload a file to Bridge | Yes |
| GET | `/files` | List uploaded files | Yes |
| DELETE | `/files/{filename}` | Delete an uploaded file | Yes |
| POST | `/harness` | Create a dynamic harness agent | Yes |
| GET | `/harness` | List dynamic harness agents | Yes |
| DELETE | `/harness/{name}` | Delete a dynamic harness agent | Yes |
| GET | `/health` | Health check | No |
| GET | `/health/agents` | Agent status | Yes |
| GET | `/stats` | Agent call statistics | Yes |
| GET | `/templates` | List prompt templates | Yes |
| POST | `/templates/render` | Render a template with variables | Yes |
| GET | `/ui` | Web UI chat interface (if enabled) | No |
| DELETE | `/sessions/{agent}/{session_id}` | Close session | Yes |

## OpenClaw Tools Proxy

ACP Bridge proxies OpenClaw's tool system, giving you a unified entry point for both agent calls and tool invocations.

### Available Tools

| Tool | Description |
|------|-------------|
| `message` | Send messages across Discord/Telegram/Slack/WhatsApp/Signal/iMessage |
| `tts` | Convert text to speech |
| `web_search` | Search the web |
| `web_fetch` | Fetch and extract content from a URL |
| `nodes` | Control paired devices (notify, run commands, camera) |
| `cron` | Manage scheduled jobs |
| `gateway` | Gateway config and restart |
| `image` | Analyze an image with AI |
| `browser` | Control browser (open, screenshot, navigate) |

### Client Usage

```bash
# List available tools
./tools/tools-client.sh -l

# Send a Discord message
./tools/tools-client.sh message send \
  --arg channel=discord \
  --arg target="channel:123456" \
  --arg message="Hello from ACP Bridge"

# Text to speech
./tools/tools-client.sh tts "Today's build passed"

# Web search
./tools/tools-client.sh web_search "Python 3.13 new features"

# Notify a Mac
./tools/tools-client.sh nodes notify \
  --arg node="office-mac" \
  --arg title="Deploy done" \
  --arg body="v1.2.3 is live"
```

### Direct API

```bash
curl -X POST http://<bridge>:18010/tools/invoke \
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

Requires `webhook.url` to be configured pointing to an OpenClaw Gateway.

## Testing

### Agent Compliance Test

Verify a CLI agent implements the ACP protocol correctly — **no Bridge required**:

```bash
bash test/test_agent_compliance.sh kiro-cli acp --trust-all-tools
bash test/test_agent_compliance.sh claude-agent-acp
bash test/test_agent_compliance.sh python3 examples/echo-agent.py
```

Covers: initialize, session/new, session/prompt (notifications + result), ping. See [AGENT_SPEC.md](AGENT_SPEC.md) for the full specification.

### Integration Tests

```bash
ACP_TOKEN=<token> bash test/test.sh http://127.0.0.1:18010
```

Run individual agent tests:

```bash
ACP_TOKEN=<token> bash test/test_codex.sh
ACP_TOKEN=<token> bash test/test_kiro.sh
ACP_TOKEN=<token> bash test/test_claude.sh
ACP_TOKEN=<token> bash test/test_qwen.sh
```

Or filter from the main runner:

```bash
ACP_TOKEN=<token> bash test/test.sh http://127.0.0.1:18010 --only codex
```

Covers: agent listing, sync/streaming calls, multi-turn conversation, Claude, Codex, async jobs, error handling.

## Process Pool

- Each `(agent, session_id)` pair maps to an independent CLI ACP subprocess
- Same session reuses subprocess across turns, context is automatically retained
- Crashed subprocesses are rebuilt automatically (context lost, user is notified)
- Idle sessions are cleaned up after TTL expiry
- `session/request_permission` is auto-replied with `allow_always` (Claude compatibility)
- LRU eviction when pool is full:
  1. Same-agent idle connection → reuse process (reset session, skip respawn)
  2. Any idle connection → evict least-recently-used
  3. All busy → return `pool_exhausted` error
- Health check every 60s: ping idle connections, kill unresponsive ones
- Ghost cleanup: kill orphaned agent processes from previous Bridge runs on startup

## Authentication

- IP allowlist + Bearer Token dual authentication
- `/health` is unauthenticated (for load balancer probes)
- Token supports `${ENV_VAR}` environment variable references
- Webhook token is configured separately from Bridge auth token

## Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| `403 forbidden` | IP not in allowlist | Add IP to `allowed_ips` |
| `401 unauthorized` | Incorrect token | Check Bearer token |
| `pool_exhausted` | Concurrency limit reached | Increase `max_processes` |
| Claude hangs | Permission request not answered | Already handled (auto-allow) |
| Discord push fails | Wrong or missing `account_id` | Use `default`, not agent name |
| Discord 500 | Bad target format | DM: `user:<id>`, channel: `channel:<id>` |
| Job stuck | Agent process anomaly | Auto-marked failed after 10min |
| Codex: not trusted dir | `/tmp` not a git repo | Add `--skip-git-repo-check` to args |
| Codex: missing LITELLM_API_KEY | Env var not passed | Set `litellm.env.LITELLM_API_KEY` in config |
| Codex: unsupported params | Bedrock rejects Codex params | Set `drop_params: true` in LiteLLM config |

## Security

See [CONTRIBUTING](CONTRIBUTING.md#security-issue-notifications) for more information.

## License

This library is licensed under the Apache License 2.0. See the [LICENSE](LICENSE) file.

## Architecture — Interaction Modes

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                    ACP Bridge Multi-Agent Interaction Modes                     │
└─────────────────────────────────────────────────────────────────────────────────┘

                            ┌─────────────────┐
                            │   User          │
                            │   (Discord/IM)  │
                            └────────┬────────┘
                                     │
    ┌────────────────┬───────────────┼───────────────┬────────────────┐
    │                │               │               │                │
    ▼                ▼               ▼               ▼                ▼
┌────────┐    ┌──────────┐    ┌──────────┐    ┌──────────┐    ┌──────────────┐
│  /cli  │    │  /chat   │    │  /jobs   │    │/pipeline │    │   /run       │
│  ────  │    │  ─────   │    │  ─────   │    │ ──────── │    │   ────       │
│Single  │    │ Session  │    │ Async    │    │ Multi-   │    │ (OpenClaw    │
│ shot   │    │  Mode    │    │ Task     │    │ Agent    │    │  native)     │
└────┬───┘    └────┬─────┘    └────┬─────┘    └────┬─────┘    └──────┬───────┘
     │             │               │               │                 │
     └─────────────┴───────────────┴───────────────┴─────────────────┘
                                   │
                                   ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│                              ACP Bridge                                         │
│  ┌─────────┐ ┌─────────┐ ┌─────────┐ ┌─────────┐ ┌──────────┐                  │
│  │  kiro   │ │ claude  │ │  codex  │ │  qwen   │ │ opencode │                  │
│  └─────────┘ └─────────┘ └─────────┘ └─────────┘ └──────────┘                  │
└─────────────────────────────────────────────────────────────────────────────────┘

═══════════════════════════════════════════════════════════════════════════════════
                               5 Interaction Modes
═══════════════════════════════════════════════════════════════════════════════════

1. /cli — Single-shot (同步单次)
   /cli ko "task"  →  agent 执行  →  结果返回
   特点：一问一答，无状态

2. /chat — Session Mode (对话模式)
   /chat ko  →  进入对话  →  后续消息自动透传  →  /chat end 退出
   特点：保持上下文，适合多轮开发

3. /jobs — Async (异步后台)
   POST /jobs { agent, prompt, target }  →  立即返回  →  完成后自动推送
   特点：非阻塞，适合长任务

4. /pipeline — Multi-Agent Orchestration (多 Agent 编排)

   mode: "sequence" — 串行接力
   ┌──────────────────────────────────────────────────────────────┐
   │  kiro ──► claude ──► codex ──► qwen ──► opencode            │
   │    │        │          │         │           │              │
   │  输出A    输出B      输出C     输出D       输出E             │
   │    └────────┴──────────┴─────────┴───────────┘              │
   │           前一个的输出作为后一个的上下文                     │
   └──────────────────────────────────────────────────────────────┘

   mode: "parallel" — 并行执行
   ┌──────────────────────────────────────────────────────────────┐
   │          ┌──► kiro ────► 输出A ──┐                          │
   │          │                       │                          │
   │  prompt ─┼──► claude ──► 输出B ──┼──► 汇总所有结果          │
   │          │                       │                          │
   │          ├──► codex ───► 输出C ──┤                          │
   │          │                       │                          │
   │          └──► qwen ────► 输出D ──┘                          │
   └──────────────────────────────────────────────────────────────┘

   mode: "race" — 竞速，最快者胜
   ┌──────────────────────────────────────────────────────────────┐
   │          ┌──► kiro ────► ✗ (running)                        │
   │  prompt ─┼──► claude ──► ✗ (running)                        │
   │          ├──► codex ───► ✓ WINNER! → 返回，取消其他         │
   │          └──► qwen ────► ✗ (running)                        │
   └──────────────────────────────────────────────────────────────┘

   mode: "random" — 随机选一个执行
   ┌──────────────────────────────────────────────────────────────┐
   │          ┌──  kiro ────  (skipped)                          │
   │  prompt ─┼──  claude ──  (skipped)                          │
   │          ├──► codex ───► ✓ CHOSEN! → 执行并返回             │
   │          └──  qwen ────  (skipped)                          │
   └──────────────────────────────────────────────────────────────┘

5. /run (sessions_spawn) — OpenClaw Native Integration
   sessions_spawn(runtime="acp", agentId="kiro", task="...")
   特点：OpenClaw 原生 API，支持 thread 绑定、streaming

═══════════════════════════════════════════════════════════════════════════════════
                                  Summary
═══════════════════════════════════════════════════════════════════════════════════

  Mode      │ Blocking │ Context │ Multi-Agent │ Use Case
 ───────────┼──────────┼─────────┼─────────────┼────────────────────────────────
  /cli      │ Yes      │ No      │ No          │ Quick one-off tasks
  /chat     │ Yes      │ Yes     │ No          │ Multi-turn development
  /jobs     │ No       │ No      │ No          │ Long-running background work
  /pipeline │ Varies   │ Varies  │ Yes ✓       │ Agent orchestration
    sequence│ Yes      │ Chained │             │   串行接力
    parallel│ Yes      │ No      │             │   并行 / 多视角
    race    │ Yes      │ No      │             │   竞速取优
    random  │ Yes      │ No      │             │   随机选一
    convers.│ Yes      │ Per-agent│            │   多轮对话协作
  /run      │ No       │ Yes     │ No          │ OpenClaw native spawn
```