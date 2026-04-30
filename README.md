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
║  IM Agents    🦞 OpenClaw  🐎 Hermes                         ║
║  CLI Agents   🤖 Claude Code  🤖 Kiro  🤖 Codex             ║
║               🤖 OpenCode  🤖 Qwen  🤖 Trae  ...           ║
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
| **Multi-agent orchestration** | Chain agents in sequence, race them in parallel, or let them debate in [conversation mode](docs/pipelines.md) |
| **Prompt-as-a-service** | Define reusable [prompt templates](docs/api-reference.md); non-technical users pick a template and fill in variables |
| **Agent marketplace** | Same [`harness-factory`](https://github.com/xiwan/harness-factory) binary + different profiles = code reviewer, DevOps helper, translator — all behind one API |

## Quick Start

### One-line install

```bash
curl -fsSL https://raw.githubusercontent.com/xiwan/acp-bridge/main/install.sh | bash
```

### Zero-config

```bash
cd acp-bridge
uv sync
uv run main.py
# No config.yaml needed — auto-detects installed agent CLIs
```

### Test it

```bash
curl -s -X POST http://localhost:18010/runs \
  -H "Authorization: Bearer $ACP_BRIDGE_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "agent_name": "kiro",
    "input": [{"parts": [{"content": "Say hello", "content_type": "text/plain"}]}]
  }'
```

📖 Docker, systemd, config file setup → [Getting Started](docs/getting-started.md)

## Documentation

| Document | Description |
|----------|-------------|
| [Getting Started](docs/getting-started.md) | Installation, Docker, prerequisites, systemd, Web UI |
| [Tutorial](docs/tutorial.md) | End-to-end: Discord → Agent → Discord in 5 minutes |
| [Configuration](docs/configuration.md) | `config.yaml` reference, Codex + LiteLLM proxy setup |
| [Agents](docs/agents.md) | Supported agents, compatibility matrix, install commands |
| [API Reference](docs/api-reference.md) | All HTTP endpoints with examples |
| [Pipelines](docs/pipelines.md) | Multi-agent orchestration: sequence, parallel, race, conversation |
| [Async Jobs](docs/async-jobs.md) | Background tasks, webhook callback, IM push |
| [Webhooks](docs/webhooks.md) | Webhook formats, auth (token vs HMAC), payload examples |
| [Client Usage](docs/client-usage.md) | `acp-client.sh` CLI client |
| [Tools Proxy](docs/tools-proxy.md) | OpenClaw tools integration |
| [Security](docs/security.md) | Auth model, deployment shapes, prompt injection |
| [Process Pool](docs/process-pool.md) | Connection lifecycle, LRU eviction, OOM protection |
| [Testing](docs/testing.md) | Agent compliance tests, integration test suite |
| [Troubleshooting](docs/troubleshooting.md) | Common issues and fixes |
| [Agent Spec](AGENT_SPEC.md) | ACP JSON-RPC protocol for writing new agents |

## Video Tutorials

| # | Title | Date | Link |
|---|-------|------|------|
| 1 | acpbridge:让龙虾和爱马仕沟通 | 2026-04-20 | [BV1QsowBAEg3](https://www.bilibili.com/video/BV1QsowBAEg3) |
| 2 | 让openclaw指挥多agent干活 | 2026-04-01 | [BV1gD9EBzEQQ](https://www.bilibili.com/video/BV1gD9EBzEQQ) |
| 3 | openclaw: acp-brige支持容器了 | 2026-03-19 | [BV1t8wyztEuM](https://www.bilibili.com/video/BV1t8wyztEuM) |
| 4 | 实现了openclaw用ACP来调用claude code | 2026-03-09 | [BV1kbPDzgE8R](https://www.bilibili.com/video/BV1kbPDzgE8R) |

## Changelog

See [CHANGELOG.md](CHANGELOG.md) for full version history. Current: v0.18.4

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines. Issues and PRs welcome.

## License

Apache License 2.0. See [LICENSE](LICENSE).
