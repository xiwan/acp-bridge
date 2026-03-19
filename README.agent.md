# ACP Bridge — Agent Execution Guide

> For you, the executing agent. Human docs: [README.md](README.md)

ACP Bridge exposes local CLI agents (Kiro/Claude/Codex) via HTTP with async job support.

```
CLI Agents (kiro/claude/codex) ↕ stdio JSON-RPC → ACP Bridge :8001 ↕ HTTP → OpenClaw → Discord/Feishu
```

## Step 1: Diagnose

Run this before any action:

```bash
echo "=== Runtime ==="
python3 --version 2>&1 || echo "PYTHON3_NOT_FOUND"
uv --version 2>&1 || echo "UV_NOT_FOUND"
node --version 2>&1 || echo "NODE_NOT_FOUND"

echo "=== Service ==="
curl -s http://127.0.0.1:8001/health 2>/dev/null || echo "BRIDGE_NOT_RUNNING"

echo "=== Config ==="
[ -f config.yaml ] && echo "CONFIG_EXISTS" || echo "CONFIG_NOT_FOUND"

echo "=== Agents ==="
which kiro-cli 2>/dev/null && echo "KIRO_OK" || echo "KIRO_NOT_FOUND"
which claude-agent-acp 2>/dev/null && echo "CLAUDE_OK" || echo "CLAUDE_NOT_FOUND"
which codex 2>/dev/null && echo "CODEX_OK" || echo "CODEX_NOT_FOUND"

echo "=== Docker ==="
sudo docker ps --filter "name=acp-bridge" --format "{{.Names}} {{.Status}}" 2>/dev/null || echo "DOCKER_N/A"
```

## Step 2: Execute by Phase

Report ✅/❌ after each phase. Skip phases whose skip condition is met.

| Phase | Task | Skip If | Reference |
|---|---|---|---|
| 1 | Install Python ≥3.12 + uv | Both found in diagnostic | `pyproject.toml` for deps |
| 2 | Install ≥1 CLI agent | At least one agent found | See agent table below |
| 3 | Create config.yaml | File exists | `cp config.yaml.example config.yaml` then edit |
| 4a | Start (native) | Health returns `ok` | `uv sync && uv run main.py` |
| 4b | Start (Docker) | Health returns `ok` | `sudo docker compose -f docker/light/docker-compose.yml up -d --build` |
| 5 | Verify | — | Run verification below |

Phase 4a/4b are mutually exclusive. Prefer 4b if Docker is available.

### Agent Install

| Agent | Mode | Install |
|---|---|---|
| Kiro CLI | ACP | `curl -fsSL https://cli.kiro.dev/install \| bash` → `kiro-cli login` |
| Claude Code | ACP | `npm i -g @zed-industries/claude-agent-acp` |
| Codex | PTY | `npm i -g @openai/codex` (needs [LiteLLM](README.md#codex--litellm-setup) for non-OpenAI models) |

## Step 3: Verify

```bash
curl -s http://127.0.0.1:8001/health
# → {"status":"ok"}

export ACP_TOKEN=<token>
curl -s http://127.0.0.1:8001/agents -H "Authorization: Bearer $ACP_TOKEN"
# → lists enabled agents

ACP_TOKEN=$ACP_TOKEN bash test/test.sh http://127.0.0.1:8001
# → 31/31 pass
```

## Key Files

| File | What Agent Should Read It For |
|---|---|
| `config.yaml.example` | All configuration options and defaults |
| `AGENT_SPEC.md` | ACP JSON-RPC protocol (required for writing new agents) |
| `src/agents.py` | Agent handler logic (ACP + PTY modes) |
| `src/acp_client.py` | Process pool + JSON-RPC connection management |
| `src/jobs.py` | Async job lifecycle |
| `src/security.py` | Auth logic (token + IP allowlist) |
| `examples/echo-agent.py` | Minimal ACP-compliant agent (~100 lines) |
| `skill/acp-client.sh` | Client usage examples |
| `test/test.sh` | Full test suite (31 cases across 5 suites) |

## API Quick Reference

| Method | Path | Auth | Purpose |
|---|---|---|---|
| GET | `/health` | No | Health check |
| GET | `/agents` | Yes | List agents |
| POST | `/runs` | Yes | Sync/streaming call (`{"agent_name":"kiro","prompt":"..."}`) |
| POST | `/jobs` | Yes | Async job submit |
| GET | `/jobs/{id}` | Yes | Job status |
| POST | `/tools/invoke` | Yes | OpenClaw tool proxy |

Auth: `Authorization: Bearer <token>` + IP in `security.allowed_ips`.

## Troubleshooting

| Symptom | Fix |
|---|---|
| `403` | Add IP to `security.allowed_ips` in config.yaml |
| `401` | Check `ACP_BRIDGE_TOKEN` env var |
| `pool_exhausted` | Increase `pool.max_processes` |
| Claude hangs | Already handled (auto-allow `session/request_permission`) |
| Codex: not trusted dir | Add `--skip-git-repo-check` to agent args |
| Job stuck >10min | Auto-marked failed by patrol |

## Execution Principles

- **Diagnose before act** — always run Step 1 first
- **Idempotent** — check state before writing; skip if correct
- **Fail fast** — stop on non-zero exit or unexpected output
- **Ask user only for secrets** — tokens, API keys
- **Report per phase** — ✅/❌ summary before proceeding
