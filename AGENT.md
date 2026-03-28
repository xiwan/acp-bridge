# ACP Bridge — Agent Execution Guide

> For you, the executing agent. Human docs: [README.md](README.md)

ACP Bridge exposes local CLI agents (Kiro/Claude/Codex) via HTTP with async job support.

```
CLI Agents (kiro/claude/codex) ↕ stdio JSON-RPC → ACP Bridge :18010 ↕ HTTP → OpenClaw → Discord/Feishu
```

---

## Step 1: Diagnose

Run this **before any action**:

```bash
echo "=== Runtime ==="
python3 --version 2>&1 || echo "PYTHON3_NOT_FOUND"
uv --version 2>&1 || echo "UV_NOT_FOUND"
node --version 2>&1 || echo "NODE_NOT_FOUND"

echo "=== Service ==="
curl -s --max-time 3 http://127.0.0.1:18010/health 2>/dev/null || echo "BRIDGE_NOT_RUNNING"

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

echo "=== Docker ==="
timeout 3 docker ps --filter "name=acp-bridge" --format "{{.Names}} {{.Status}}" 2>/dev/null || echo "DOCKER_N/A"

echo "=== Systemd ==="
timeout 3 systemctl is-active acp-bridge 2>/dev/null || echo "SYSTEMD_N/A"
```

---

## Step 2: Install & Configure

Report ✅/❌ after each phase. **Skip phases whose skip condition is met.**

| Phase | Task | Skip If |
|---|---|---|
| 2.1 | Install Python ≥3.12 + uv | Both found in diagnostic |
| 2.2 | Install ≥1 CLI agent (see table below) | At least one agent found |
| 2.3 | `cp config.yaml.example config.yaml` and edit | `CONFIG_EXISTS` |
| 2.4 | Set environment variables (see below) | All env vars show `=set` |

### Agent Install

| Agent | Mode | Install |
|---|---|---|
| Kiro CLI | ACP | `curl -fsSL https://cli.kiro.dev/install \| bash` → `kiro-cli login` |
| Claude Code | ACP | `npm i -g @zed-industries/claude-agent-acp` |
| Codex | PTY | `npm i -g @openai/codex` (needs LiteLLM for non-OpenAI models, see [README.md](README.md#codex--litellm-setup)) |

### Environment Variables

These are referenced by `config.yaml` via `${VAR}` syntax. **Ask user for values if not set.**

| Variable | Required | Purpose |
|---|---|---|
| `ACP_BRIDGE_TOKEN` | Yes | Bridge auth token (`security.auth_token`) |
| `OPENCLAW_TOKEN` | If using webhook | OpenClaw gateway token (`webhook.token`) |
| `LITELLM_API_KEY` | If using Codex | LiteLLM proxy auth (`litellm.env`) |
| `CLAUDE_CODE_USE_BEDROCK` | If Claude on Bedrock | Set to `1` |
| `ANTHROPIC_MODEL` | If Claude on Bedrock | e.g. `global.anthropic.claude-sonnet-4-6` |

---

## Step 3: Start the Service

**Ask user which method they prefer, recommend Docker if available.**

### Option A: Docker (recommended)

Lightweight image (~439MB). Agent CLIs stay on host, mounted into container.

```bash
# 1. Create .env for Docker
cp docker/light/.env.example docker/light/.env
# Edit docker/light/.env — set ACP_BRIDGE_TOKEN and other tokens

# 2. Edit docker/light/docker-compose.yml
#    Uncomment volume mounts for installed agents

# 3. Build and start
sudo docker compose -f docker/light/docker-compose.yml up -d --build

# 4. Check
sudo docker compose -f docker/light/docker-compose.yml logs --tail 20
curl -s --max-time 3 http://127.0.0.1:18010/health
```

> ⚠️ `sudo` does NOT pass shell env vars. Tokens must go in `docker/light/.env` file.

### Option B: systemd

```bash
# 1. Set env vars in shell profile (~/.bashrc or ~/.bash_profile)
export ACP_BRIDGE_TOKEN="<token>"
export OPENCLAW_TOKEN="<token>"

# 2. Add env vars to systemd unit (edit acp-bridge.service)
#    Append Environment= lines under [Service]:
#    Environment=ACP_BRIDGE_TOKEN=<token>
#    Environment=OPENCLAW_TOKEN=<token>

# 3. Install and start
sudo cp acp-bridge.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now acp-bridge

# 4. Check
sudo systemctl status acp-bridge
sudo journalctl -u acp-bridge -f --no-pager -n 20
curl -s --max-time 3 http://127.0.0.1:18010/health
```

### Option C: nohup (quick & dirty)

```bash
# 1. Export env vars
export ACP_BRIDGE_TOKEN="<token>"
export OPENCLAW_TOKEN="<token>"

# 2. Start
cd /path/to/acp-bridge
nohup uv run main.py > /tmp/acp-bridge.log 2>&1 &
echo $! > /tmp/acp-bridge.pid

# 3. Check
curl -s --max-time 3 http://127.0.0.1:18010/health
tail -f /tmp/acp-bridge.log
```

---

## Step 4: Verify

```bash
# Health
curl -s --max-time 3 http://127.0.0.1:18010/health
# → {"status":"ok"}

# Agents
export ACP_TOKEN=<token>
curl -s http://127.0.0.1:18010/agents -H "Authorization: Bearer $ACP_TOKEN"

# Full test suite (31 cases)
ACP_TOKEN=$ACP_TOKEN bash test/test.sh http://127.0.0.1:18010
```

---

## Key Files

| File | Read It For |
|---|---|
| `config.yaml.example` | All configuration options and defaults |
| `AGENT_SPEC.md` | ACP JSON-RPC protocol spec (for writing new agents) |
| `acp-bridge.service` | systemd unit template |
| `docker/light/docker-compose.yml` | Docker volume mounts per agent |
| `docker/light/.env.example` | Docker env var template |
| `src/agents.py` | Agent handler logic (ACP + PTY modes) |
| `src/acp_client.py` | Process pool + JSON-RPC management |
| `src/jobs.py` | Async job lifecycle |
| `examples/echo-agent.py` | Minimal ACP agent (~100 lines) |
| `skill/acp-client.sh` | Client usage examples |
| `test/test.sh` | Full test suite (31 cases, 5 suites) |

## API Quick Reference

| Method | Path | Auth | Purpose |
|---|---|---|---|
| GET | `/health` | No | Health check |
| GET | `/agents` | Yes | List agents |
| POST | `/runs` | Yes | Sync/streaming call |
| POST | `/jobs` | Yes | Async job submit |
| GET | `/jobs/{id}` | Yes | Job status |
| POST | `/tools/invoke` | Yes | OpenClaw tool proxy |
| POST | `/chat/messages` | Yes | Save chat message (Web UI) |
| GET | `/chat/messages` | Yes | Load recent messages (Web UI) |
| DELETE | `/chat/messages` | Yes | Clear chat history (Web UI) |
| POST | `/chat/fold` | Yes | Fold session messages (Web UI) |
| GET | `/ui` | No | Web UI (if `--ui` enabled) |

Auth: `Authorization: Bearer <token>` + IP in `security.allowed_ips`.

## Optional: SSH Reverse Tunnel to Remote Host

If the bridge runs locally (e.g. laptop) and you need to expose it to a remote server, use an SSH reverse tunnel.

### Prerequisites

- SSH key pair (e.g. `~/.ssh/id_ed25519_ec2` + `.pub`)
- Remote host reachable via SSH
- If using EC2 Instance Connect, push the public key first:

```bash
aws ec2-instance-connect send-ssh-public-key \
    --instance-id <INSTANCE_ID> \
    --instance-os-user <USER> \
    --ssh-public-key file://~/.ssh/<KEY>.pub \
    --region <REGION>
```

### Start Tunnel

```bash
ssh -i ~/.ssh/<KEY> \
    -o StrictHostKeyChecking=no \
    -o ServerAliveInterval=30 \
    -R 18010:127.0.0.1:18010 \
    <USER>@<REMOTE_IP> -N
```

This makes `127.0.0.1:18010` on the remote host forward to your local bridge.

### Lifecycle Management

For a unified start/stop workflow, create a local `start.sh` (gitignored) that:

1. Starts `uv run main.py` and waits for `/health` to return OK
2. Pushes SSH key (if using EC2 Instance Connect) and opens the reverse tunnel
3. On `Ctrl+C` / exit (via `trap EXIT`):
   - Kills the SSH tunnel process
   - Reconnects briefly to the remote host to run `fuser -k 18010/tcp` to clean up any lingering sshd listener
   - Lets the bridge process exit

> ⚠️ `start.sh` contains host-specific values — it is already in `.gitignore`.

### Cleanup Stale Remote Listeners

If a tunnel was interrupted without clean shutdown:

```bash
# On the remote host
fuser -k 18010/tcp
```

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| `403` | Add IP to `security.allowed_ips` |
| `401` | Check `ACP_BRIDGE_TOKEN` env var |
| `pool_exhausted` | Increase `pool.max_processes` |
| Claude hangs | Already handled (auto-allow) |
| Codex: not trusted dir | Add `--skip-git-repo-check` to args |
| Job stuck >10min | Auto-marked failed by patrol |
| Docker: env vars not passed | Use `.env` file, not shell export with `sudo` |
| systemd: env vars missing | Add `Environment=` lines to `.service` file |

## Execution Principles

- **Diagnose before act** — always run Step 1 first
- **Idempotent** — check state before writing; skip if correct
- **Fail fast** — stop on non-zero exit or unexpected output
- **Ask user only for secrets** — tokens, API keys, startup method preference
- **Report per phase** — ✅/❌ summary before proceeding
