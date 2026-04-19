```
╔══════════════════════════════════════════════════════════════╗
║                                                              ║
║      _   ___ ___   ___      _    _                           ║
║     /_\ / __| _ \ | _ )_ __(_)__| |__ _  ___                ║
║    / _ \ (__| _/  | _ \ '_|| / _` / _` |/ -_)               ║
║   /_/ \_\___|_|   |___/|_| |_\__,_\__, \___|                ║
║                                    |___/                     ║
║          https://github.com/xiwan/acp-bridge                 ║
║                                                              ║
╠══════════════════════════════════════════════════════════════╣
║                                                              ║
║  IM Agents    🦞 OpenClaw  🤖 Hermes                         ║
║  CLI Agents   🤖 Claude Code  🤖 Kiro  🤖 Codex             ║
║               🤖 OpenCode  🤖 Qwen  ...                     ║
║  Lite Agents  🏭 Harness Agents                              ║
║                                                              ║
╚══════════════════════════════════════════════════════════════╝

    Multi-Agent Mesh · Connect · Orchestrate · Scale
```

# ACP Bridge

[![GitHub stars](https://img.shields.io/github/stars/xiwan/acp-bridge?style=social)](https://github.com/xiwan/acp-bridge/stargazers)
[![GitHub forks](https://img.shields.io/github/forks/xiwan/acp-bridge?style=social)](https://github.com/xiwan/acp-bridge/network/members)
[![GitHub Discussions](https://img.shields.io/github/discussions/xiwan/acp-bridge?logo=github&label=Discussions)](https://github.com/xiwan/acp-bridge/discussions)
[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)

[![Agent Guide](https://img.shields.io/badge/Agent_Guide-for_AI_Agents-blue?logo=robot)](AGENT.md)
[![AWS Blog](https://img.shields.io/badge/AWS_Blog-Published-orange?logo=amazonaws)](https://aws.amazon.com/cn/blogs/china/enable-kiro-and-claude-code-for-im-with-acp-bridge-async-ai-workflow/)

A multi-agent orchestration platform that exposes local CLI agents (Kiro, Claude Code, Codex, Qwen, OpenCode, Hermes, etc.) as HTTP services via [ACP](https://agentclientprotocol.com/), with pipeline orchestration and IM-driven async workflows.

## Why ACP Bridge

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

### Gateway — expose local agents as HTTP services

ACP protocol over stdio, process pool with session reuse, sync + SSE streaming, Bearer Token + IP allowlist auth. See [Process Pool](docs/process-pool.md) and [Security](docs/security.md).

### Orchestration — multi-agent pipelines

Sequence, parallel, race, and conversation modes. Profile-driven lightweight agents via [harness-factory](https://github.com/xiwan/harness-factory). See [Pipelines](docs/pipelines.md).

### IM & Async Workflows — from chat to code

Async jobs with webhook callback, Discord/Feishu/Telegram push via OpenClaw or Hermes, Web UI with chat persistence. See [Async Jobs](docs/async-jobs.md) and [Tools Proxy](docs/tools-proxy.md).

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
| [Hermes Agent](https://github.com/NousResearch/hermes-agent) | Nous Research | ✅ `hermes acp` | `acp` | ✅ Integrated | 8/8 |
| [CoStrict](https://github.com/zgsm-ai/costrict) | Open Source 🇨🇳 | ✅ Native | — | 🟡 Planned | — |
| [Trae Agent](https://github.com/bytedance/trae-agent) | ByteDance 🇨🇳 | ❌ | — | ⚪ No ACP | — |
| [Aider](https://github.com/Aider-AI/aider) | Open Source | ❌ | — | ⚪ No ACP | — |

**Legend:** ✅ Integrated — 🟡 Planned (ACP-ready) — ⚪ No ACP support yet — 🧪 Experimental

> Agents without ACP can still be integrated via PTY mode (like Codex). PRs welcome!

## Quick Start

### One-line install

```bash
curl -fsSL https://raw.githubusercontent.com/xiwan/acp-bridge/main/install.sh | bash
```

### Zero-config

```bash
cd acp-bridge && uv sync && uv run main.py
```

📖 See [Getting Started](docs/getting-started.md) for Docker, manual config, and more.

## Documentation

| Document | Description |
|----------|-------------|
| [Getting Started](docs/getting-started.md) | Installation, Docker, prerequisites |
| [Configuration](docs/configuration.md) | config.yaml reference, Codex + LiteLLM setup |
| [Agents](docs/agents.md) | Supported agents and compatibility matrix |
| [Pipelines](docs/pipelines.md) | Multi-agent orchestration modes |
| [Async Jobs](docs/async-jobs.md) | Background tasks, webhooks, IM push |
| [API Reference](docs/api-reference.md) | HTTP endpoints |
| [Client Usage](docs/client-usage.md) | CLI client (acp-client.sh) |
| [Tools Proxy](docs/tools-proxy.md) | OpenClaw tools integration |
| [Security](docs/security.md) | Auth, deployment, prompt injection |
| [Process Pool](docs/process-pool.md) | Connection management and lifecycle |
| [Testing](docs/testing.md) | Compliance and integration tests |
| [Troubleshooting](docs/troubleshooting.md) | Common issues and fixes |

## Changelog

See [CHANGELOG.md](CHANGELOG.md) for full version history. Current: v0.15.11

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
│   ├── pipeline.py      # Multi-agent pipeline (sequence, parallel, race, conversation) + shared workspace
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
│   ├── test_hermes.sh             # Hermes agent tests
│   └── reports/                   # Test reports
├── AGENT_SPEC.md        # ACP agent integration specification
├── config.yaml          # Service configuration (auto-generated or manual)
├── pyproject.toml
└── uv.lock
```

## Contributing

See [CONTRIBUTING](CONTRIBUTING.md) for more information.

## License

This library is licensed under the Apache License 2.0. See the [LICENSE](LICENSE) file.
