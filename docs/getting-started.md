# Getting Started

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
