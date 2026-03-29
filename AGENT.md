# ACP Bridge — Agent Execution Guide

> You are the executing agent. Human docs → [README.md](README.md)

ACP Bridge exposes local CLI agents (Kiro/Claude/Codex/Qwen/OpenCode) via HTTP with async job support.

```
CLI Agents (kiro/claude/codex/qwen/...) ↕ stdio JSON-RPC → ACP Bridge :18010 ↕ HTTP → OpenClaw / Web UI / HTTP API
```

---

## Phase 1: Diagnose (🤖 auto)

Run this block, parse the output, then proceed to Phase 2.

```bash
echo "=== Runtime ==="
python3 --version 2>&1 || echo "PYTHON3_NOT_FOUND"
uv --version 2>&1 || echo "UV_NOT_FOUND"
node --version 2>&1 || echo "NODE_NOT_FOUND"

echo "=== Config ==="
[ -f config.yaml ] && echo "CONFIG_EXISTS" || echo "CONFIG_NOT_FOUND"

echo "=== Env Vars ==="
[ -n "$ACP_BRIDGE_TOKEN" ] && echo "ACP_BRIDGE_TOKEN=set" || echo "ACP_BRIDGE_TOKEN=NOT_SET"
[ -n "$OPENCLAW_TOKEN" ] && echo "OPENCLAW_TOKEN=set" || echo "OPENCLAW_TOKEN=NOT_SET"
[ -n "$LITELLM_API_KEY" ] && echo "LITELLM_API_KEY=set" || echo "LITELLM_API_KEY=NOT_SET"

echo "=== Agents ==="
which kiro-cli 2>/dev/null && echo "KIRO_OK" || echo "KIRO_NOT_FOUND"
which claude-agent-acp 2>/dev/null && echo "CLAUDE_OK" || echo "CLAUDE_NOT_FOUND"
which codex 2>/dev/null && echo "CODEX_OK" || echo "CODEX_NOT_FOUND"
which qwen 2>/dev/null && echo "QWEN_OK" || echo "QWEN_NOT_FOUND"
which opencode 2>/dev/null && echo "OPENCODE_OK" || echo "OPENCODE_NOT_FOUND"

echo "=== Service ==="
curl -s --max-time 3 http://127.0.0.1:18010/health 2>/dev/null || echo "BRIDGE_NOT_RUNNING"

echo "=== Docker ==="
timeout 3 docker ps --filter "name=acp-bridge" --format "{{.Names}} {{.Status}}" 2>/dev/null || echo "DOCKER_N/A"

echo "=== Systemd ==="
timeout 3 systemctl is-active acp-bridge 2>/dev/null || echo "SYSTEMD_N/A"
```

---

## Phase 2: Collect Human Input (👤 ask once)

Based on diagnostic results, ask the human for **only** what you cannot determine yourself. Collect everything in one prompt.

| What | Ask If | Example |
|---|---|---|
| `ACP_BRIDGE_TOKEN` | `NOT_SET` | Any string, e.g. `my-secret-token` |
| `OPENCLAW_TOKEN` | `NOT_SET` and human wants webhook/Discord push | OpenClaw gateway token |
| `LITELLM_API_KEY` | `NOT_SET` and Codex/Qwen agent is needed | LiteLLM proxy token |
| `CLAUDE_CODE_USE_BEDROCK` | Claude agent needed on Bedrock | `1` |
| `ANTHROPIC_MODEL` | Claude agent needed on Bedrock | e.g. `global.anthropic.claude-sonnet-4-6` |
| Startup method | Always (unless already running) | Docker / systemd / nohup |

> If all env vars are set and service is running → skip to Phase 5.

---

## Phase 3: Install & Configure (🤖 auto)

Execute each step. Skip if diagnostic shows it's already done.

| Step | Action | Skip If |
|---|---|---|
| 3.1 | Install Python ≥3.12 + uv | Both found |
| 3.2 | Install CLI agent(s) — see table below | At least one found |
| 3.3 | `cp config.yaml.example config.yaml` | `CONFIG_EXISTS` |
| 3.4 | Set env vars from Phase 2 answers | All `=set` |

### Agent Install Reference

| Agent | Mode | Install |
|---|---|---|
| Kiro CLI | ACP | `curl -fsSL https://cli.kiro.dev/install \| bash` → `kiro-cli login` |
| Claude Code | ACP | `npm i -g @zed-industries/claude-agent-acp` |
| Codex | PTY | `npm i -g @openai/codex` (needs LiteLLM, see [README](README.md#codex--litellm-setup)) |
| Qwen Code | ACP | `npm i -g @anthropic-ai/qwen-code` |
| OpenCode | ACP | See [opencode-ai/opencode](https://github.com/opencode-ai/opencode) |

---

## Phase 4: Start (🤖 auto, using human's choice)

### Docker (recommended)

```bash
cp docker/light/.env.example docker/light/.env
# Write tokens into docker/light/.env
sudo docker compose -f docker/light/docker-compose.yml up -d --build
```

> ⚠️ `sudo` does NOT pass shell env vars. Tokens must go in `.env` file.

### systemd

```bash
sudo cp acp-bridge.service /etc/systemd/system/
# Add Environment= lines for tokens in the .service file
sudo systemctl daemon-reload
sudo systemctl enable --now acp-bridge
```

### nohup

```bash
nohup uv run main.py > /tmp/acp-bridge.log 2>&1 &
echo $! > /tmp/acp-bridge.pid
```

---

## Phase 5: Verify (🤖 auto)

```bash
# Health
curl -s --max-time 3 http://127.0.0.1:18010/health
# → {"status":"ok"}

# Agents
curl -s http://127.0.0.1:18010/agents -H "Authorization: Bearer $ACP_BRIDGE_TOKEN"

# Full test suite
ACP_TOKEN=$ACP_BRIDGE_TOKEN bash test/test.sh http://127.0.0.1:18010
```

Report ✅/❌ to human. Done.

---

## Reference

### API Quick Reference

| Method | Path | Auth | Purpose |
|---|---|---|---|
| GET | `/health` | No | Health check |
| GET | `/health/agents` | Yes | Agent status |
| GET | `/agents` | Yes | List agents |
| POST | `/runs` | Yes | Sync/streaming call |
| POST | `/jobs` | Yes | Async job submit |
| GET | `/jobs` | Yes | List all jobs + stats |
| GET | `/jobs/{id}` | Yes | Job status |
| GET | `/tools` | Yes | List available OpenClaw tools |
| POST | `/tools/invoke` | Yes | OpenClaw tool proxy |
| POST | `/chat/messages` | Yes | Save chat message (Web UI) |
| GET | `/chat/messages` | Yes | Load recent messages (Web UI) |
| DELETE | `/chat/messages` | Yes | Clear chat history (Web UI) |
| POST | `/chat/fold` | Yes | Fold session messages (Web UI) |
| GET | `/ui` | No | Web UI (if `--ui` enabled) |
| DELETE | `/sessions/{agent}/{session_id}` | Yes | Close session |

Auth: `Authorization: Bearer <token>` + IP in `security.allowed_ips`.

### Key Files

| File | Purpose |
|---|---|
| `config.yaml.example` | All configuration options and defaults |
| `AGENT_SPEC.md` | ACP JSON-RPC protocol spec (for writing new agents) |
| `src/agents.py` | Agent handler logic (ACP + PTY modes) |
| `src/acp_client.py` | Process pool + JSON-RPC management |
| `src/jobs.py` | Async job lifecycle |
| `src/sse.py` | Notification → SSE event transform |
| `src/formatters.py` | IM channel formatters (Discord/Feishu) |
| `src/store.py` | SQLite job persistence |
| `src/routes/*.py` | Route registration (jobs, tools, health, chat) |
| `examples/echo-agent.py` | Minimal ACP agent (~100 lines) |
| `skill/acp-client.sh` | Client usage examples |
| `test/test.sh` | Full test suite |

### SSH Reverse Tunnel (optional)

If the bridge runs locally and you need to expose it to a remote server:

```bash
ssh -i ~/.ssh/<KEY> \
    -o StrictHostKeyChecking=no \
    -o ServerAliveInterval=30 \
    -R 18010:127.0.0.1:18010 \
    <USER>@<REMOTE_IP> -N
```

Cleanup stale listeners on remote: `fuser -k 18010/tcp`

### Troubleshooting

| Symptom | Fix |
|---|---|
| `403` | Add IP to `security.allowed_ips` |
| `401` | Check `ACP_BRIDGE_TOKEN` env var |
| `pool_exhausted` | Increase `pool.max_processes` |
| Claude hangs | Already handled (auto-allow) |
| Codex: not trusted dir | Add `--skip-git-repo-check` to args |
| Codex: missing LITELLM_API_KEY | Set `litellm.env.LITELLM_API_KEY` in config |
| Codex: unsupported params | Set `drop_params: true` in LiteLLM config |
| Job stuck >10min | Auto-marked failed by patrol |
| Docker: env vars not passed | Use `.env` file, not shell export with `sudo` |
| systemd: env vars missing | Add `Environment=` lines to `.service` file |
