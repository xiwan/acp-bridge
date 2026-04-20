[← README](../README.md) | [Tutorial →](tutorial.md)

> **Docs:** [Getting Started](getting-started.md) · [Tutorial](tutorial.md) · [Configuration](configuration.md) · [Agents](agents.md) · [API Reference](api-reference.md) · [Pipelines](pipelines.md) · [Async Jobs](async-jobs.md) · [Client Usage](client-usage.md) · [Tools Proxy](tools-proxy.md) · [Security](security.md) · [Process Pool](process-pool.md) · [Testing](testing.md) · [Troubleshooting](troubleshooting.md)

# Getting Started

ACP Bridge is a local HTTP gateway that turns CLI agents (Kiro, Claude Code, Codex, etc.) into a unified REST API. Once running, any HTTP client — your team, your bots, your IM — can call these agents without installing them locally. Auth tokens protect access; see [Security](security.md) for details.

```
  IM (Discord/Feishu)          HTTP client            Web UI
        │                          │                     │
        ▼                          ▼                     ▼
  ┌───────────┐            ┌──────────────┐        ┌─────────┐
  │  OpenClaw │───HTTP────▶│  ACP Bridge  │◀──────▶│  /ui    │
  │  / Hermes │            │  :18010      │        └─────────┘
  └───────────┘            └──────┬───────┘
                                  │ stdio JSON-RPC (ACP) / PTY
                    ┌─────────────┼─────────────┐
                    ▼             ▼              ▼
               ┌────────┐  ┌────────┐     ┌────────┐
               │  Kiro  │  │ Claude │ ... │ Codex  │
               └────────┘  └────────┘     └────────┘
```

## Prerequisites

- **Python >= 3.12**
- **[uv](https://docs.astral.sh/uv/)** — fast Python package manager
- **At least one CLI agent installed** — for example:
  ```bash
  curl -fsSL https://cli.kiro.dev/install | bash          # Kiro
  npm i -g @agentclientprotocol/claude-agent-acp           # Claude Code
  npm i -g @openai/codex                                   # Codex
  ```
  → Full list with install commands: [Agents](agents.md)
- Client dependencies: `curl`, `jq`, `uuidgen`
- For Codex with non-OpenAI models: [Node.js](https://nodejs.org/), [LiteLLM](https://github.com/BerriAI/litellm) — see [Configuration](configuration.md#codex--litellm-setup)

## Install

### One-line install (recommended)

```bash
curl -fsSL https://raw.githubusercontent.com/xiwan/acp-bridge/main/install.sh | bash
```

The interactive installer will:

1. Detect installed agent CLIs in your `PATH`
2. Prompt you to select which agents to enable
3. Configure auth tokens (or generate one)
4. Generate `config.yaml` with your settings
5. Set up systemd services (`acp-bridge.service` and optionally `litellm.service`)
6. Start the Bridge and verify health

**Optional — OpenClaw IM integration:** If you use [OpenClaw](https://github.com/NousResearch/hermes-agent) as an IM gateway (Discord/Feishu/Telegram), the installer also prints connection info:

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

If you don't use OpenClaw, ignore this — Bridge works standalone via HTTP.

### Zero-config vs config file

| Mode | When to use |
|------|-------------|
| **Zero-config** | Quick start, agents already in PATH, default settings are fine |
| **Config file** | Need fixed token, custom agent paths, webhook/IM push, pool tuning, or LiteLLM proxy |

#### Zero-config (auto-detect agents in PATH)

```bash
cd acp-bridge
uv sync
uv run main.py
# No config.yaml needed — auto-detects installed agent CLIs
# Prints a random auth token on startup; set ACP_BRIDGE_TOKEN env for a fixed one
```

#### With config file

```bash
cd acp-bridge
cp config.yaml.example config.yaml
# Edit config.yaml with your settings
uv sync
uv run main.py
```

See [Configuration](configuration.md) for the full `config.yaml` reference.

### Command-line flags

| Flag | Description |
|------|-------------|
| `--ui` | Enable the built-in Web UI at `/ui` |
| `--host 0.0.0.0` | Bind address (default: from config or `0.0.0.0`) |
| `--port 18010` | Listen port (default: from config or `18010`) |

## Docker

> Docker and `uv run` are **alternative** startup methods — pick one, not both. Docker is recommended for production; `uv run` is simpler for local development.

A lightweight Docker image containing only the ACP Bridge gateway. Agent CLIs stay on your host and are **mounted into the container** as read-only volumes — this avoids bloating the image and lets you manage agent versions independently.

Why not install agents inside the container? Agent CLIs (Kiro, Claude, Codex) have their own auth state, config files, and runtime dependencies. Mounting from the host keeps a single source of truth.

```bash
# 1. Prepare config
cp config.yaml.example config.yaml
# Edit config.yaml with your settings

# 2. Edit docker/light/docker-compose.yml
#    Uncomment volume mounts for the agents you have installed

# 3. Set environment variables
cp docker/light/.env.example docker/light/.env
# Edit docker/light/.env with your tokens

# 4. Build and run
sudo docker compose -f docker/light/docker-compose.yml up -d --build

# Check logs
sudo docker compose -f docker/light/docker-compose.yml logs -f
```

### Agent mount examples

Each agent needs its binary and config directory mounted. From `docker-compose.yml`:

```yaml
volumes:
  # Kiro CLI — native binary + auth data
  - /home/ec2-user/.local/bin/kiro-cli:/usr/local/bin/kiro-cli:ro
  - /home/ec2-user/.kiro:/home/app/.kiro

  # Claude Code — npm global module
  - /usr/lib/node_modules/@agentclientprotocol:/usr/lib/node_modules/@agentclientprotocol:ro

  # Codex — npm global module + config
  - /usr/lib/node_modules/@openai:/usr/lib/node_modules/@openai:ro
  - /home/ec2-user/.codex:/home/app/.codex:ro

  # Harness Factory — single binary
  - /path/to/harness-factory:/usr/local/bin/harness-factory:ro
```

Adjust paths to match your host. See `docker/light/docker-compose.yml` for the full list.

> **Note:** When using `sudo`, shell environment variables and `~` paths are NOT passed to Docker. Use a `.env` file or pass variables inline:
>
> ```bash
> sudo ACP_BRIDGE_TOKEN=<token> CLAUDE_CODE_USE_BEDROCK=1 \
>   docker compose -f docker/light/docker-compose.yml up -d
> ```

See `docker/light/docker-compose.yml` for mount examples for each agent.

## systemd Management

> systemd services are created by the **one-line installer** (`install.sh`). If you started Bridge manually with `uv run` or Docker, this section does not apply.

The installer sets up systemd services automatically. Use `bridge-ctl.sh` for lifecycle management:

| Command | Description |
|---------|-------------|
| `./bridge-ctl.sh status` | Show service status |
| `./bridge-ctl.sh logs 100` | View last 100 log lines |
| `./bridge-ctl.sh health` | Check `/health` endpoint |
| `./bridge-ctl.sh restart` | Restart Bridge (kills running agent subprocesses) |
| `./bridge-ctl.sh restart-all` | Restart LiteLLM + Bridge |
| `./bridge-ctl.sh stop` | Stop Bridge |

## Verify Installation

```bash
# Option 1: CLI client (recommended — simplest)
export ACP_BRIDGE_URL=http://localhost:18010
export ACP_TOKEN=$ACP_BRIDGE_TOKEN
./skill/scripts/acp-client.sh -l                        # list agents
./skill/scripts/acp-client.sh -a kiro "Say hello"       # call an agent

# Option 2: curl
curl -s http://localhost:18010/health
# → {"status":"ok","version":"0.15.11"}

curl -s --max-time 120 -X POST http://localhost:18010/runs \
  -H "Authorization: Bearer $ACP_BRIDGE_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "agent_name": "kiro",
    "input": [{"parts": [{"content": "Say hello in one sentence", "content_type": "text/plain"}]}]
  }'
```

> **Tip:** `acp-client.sh` wraps the verbose ACP JSON format for you. See [Client Usage](client-usage.md) for all options.

## Web UI

Enable the built-in chat interface with the `--ui` flag or `server.ui: true` in config:

```bash
uv run main.py --ui
# Open http://localhost:18010/ui in your browser
```

Features: agent selection, chat persistence (SQLite), message folding, dark mode, responsive layout.

## Remote Access

> If your Bridge runs on a cloud instance (EC2, GCP VM, etc.) without a public IP or with restricted security groups, SSH tunneling is the simplest way to access it from your local machine.

```bash
# Forward remote Bridge to local port
ssh -i ~/.ssh/<KEY> -L 18010:127.0.0.1:18010 <USER>@<REMOTE_IP> -N

# Then access locally:
curl http://127.0.0.1:18010/health
# Web UI: http://127.0.0.1:18010/ui
```

The tunnel maps remote `:18010` to your local `:18010`. Keep the SSH session open while using Bridge. Add `-f` to run in background.

## Next Steps

- [Configuration](configuration.md) — full `config.yaml` reference
- [Agents](agents.md) — install and configure specific agents
- [API Reference](api-reference.md) — all HTTP endpoints
- [Client Usage](client-usage.md) — CLI client for quick testing
- [Security](security.md) — auth model and deployment recommendations
