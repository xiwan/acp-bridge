[← README](../README.md) | [Configuration →](configuration.md)

> **Docs:** [Getting Started](getting-started.md) · [Configuration](configuration.md) · [Agents](agents.md) · [API Reference](api-reference.md) · [Pipelines](pipelines.md) · [Async Jobs](async-jobs.md) · [Client Usage](client-usage.md) · [Tools Proxy](tools-proxy.md) · [Security](security.md) · [Process Pool](process-pool.md) · [Testing](testing.md) · [Troubleshooting](troubleshooting.md)

# Getting Started

## Prerequisites

- **Python >= 3.12**
- **[uv](https://docs.astral.sh/uv/)** — fast Python package manager
- At least one CLI agent installed (e.g. `kiro-cli`, `claude-agent-acp`, `codex`)
- Client dependencies: `curl`, `jq`, `uuidgen`
- For Codex: [Node.js](https://nodejs.org/) (npm), [LiteLLM](https://github.com/BerriAI/litellm) (if using non-OpenAI models via proxy)

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

On completion, the installer prints the **OpenClaw skill setup info**:

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

Tell your OpenClaw bot to install the skill at the URL above, then set `ACP_TOKEN` and `ACP_BRIDGE_URL` so it can reach the Bridge.

### Zero-config (auto-detect agents in PATH)

No `config.yaml` needed — Bridge scans your `PATH` for known agent CLIs and registers them automatically.

```bash
cd acp-bridge
uv sync
uv run main.py
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

See [Configuration](configuration.md) for the full `config.yaml` reference.

### Command-line flags

| Flag | Description |
|------|-------------|
| `--ui` | Enable the built-in Web UI at `/ui` |
| `--host 0.0.0.0` | Bind address (default: from config or `0.0.0.0`) |
| `--port 18010` | Listen port (default: from config or `18010`) |

## Docker

A lightweight Docker image containing only the ACP Bridge gateway. Agent CLIs (Kiro, Claude Code, Codex) stay on your host — mount them into the container as needed.

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

> **Note:** When using `sudo`, shell environment variables and `~` paths are NOT passed to Docker. Use a `.env` file or pass variables inline:
>
> ```bash
> sudo ACP_BRIDGE_TOKEN=<token> CLAUDE_CODE_USE_BEDROCK=1 \
>   docker compose -f docker/light/docker-compose.yml up -d
> ```

See `docker/light/docker-compose.yml` for mount examples for each agent.

## systemd Management

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
# Health check (no auth required)
curl -s http://localhost:18010/health
# → {"status":"ok","version":"0.15.11"}

# List agents (auth required)
curl -s http://localhost:18010/agents \
  -H "Authorization: Bearer $ACP_BRIDGE_TOKEN"

# Test an agent call (first call may take 5-15s for cold start)
curl -s --max-time 120 -X POST http://localhost:18010/runs \
  -H "Authorization: Bearer $ACP_BRIDGE_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "agent_name": "kiro",
    "input": [{"parts": [{"content": "Say hello in one sentence", "content_type": "text/plain"}]}]
  }'
```

## Web UI

Enable the built-in chat interface with the `--ui` flag or `server.ui: true` in config:

```bash
uv run main.py --ui
# Open http://localhost:18010/ui in your browser
```

Features: agent selection, chat persistence (SQLite), message folding, dark mode, responsive layout.

## Remote Access

If Bridge runs on a remote machine (EC2, cloud VM), use SSH tunneling:

```bash
# Forward remote Bridge to local port
ssh -i ~/.ssh/<KEY> -L 18010:127.0.0.1:18010 <USER>@<REMOTE_IP> -N

# Then access locally:
curl http://127.0.0.1:18010/health
# Web UI: http://127.0.0.1:18010/ui
```

## Next Steps

- [Configuration](configuration.md) — full `config.yaml` reference
- [Agents](agents.md) — install and configure specific agents
- [API Reference](api-reference.md) — all HTTP endpoints
- [Client Usage](client-usage.md) — CLI client for quick testing
- [Security](security.md) — auth model and deployment recommendations
