# ACP Bridge — Agent Execution Guide

> You are the executing agent. Human docs → [README.md](README.md)

ACP Bridge exposes local CLI agents (Kiro/Claude/Codex/Qwen/OpenCode/Hermes/Harness Factory) via HTTP with async job support.
Harness Factory 0.6.0+ has a built-in model registry (8 models, 7 providers) with `"auto"` random selection and error fallback.

```
CLI Agents (kiro/claude/codex/qwen/harness-factory/...) ↕ stdio JSON-RPC → ACP Bridge :18010 ↕ HTTP → OpenClaw / Web UI / HTTP API
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
which hermes 2>/dev/null && echo "HERMES_OK" || echo "HERMES_NOT_FOUND"
which harness-factory 2>/dev/null && echo "HARNESS_OK" || echo "HARNESS_NOT_FOUND"

echo "=== Service ==="
curl -s --max-time 3 http://127.0.0.1:18010/health 2>/dev/null || echo "BRIDGE_NOT_RUNNING"

echo "=== Docker ==="
timeout 3 docker ps --filter "name=acp-bridge" --format "{{.Names}} {{.Status}}" 2>/dev/null || echo "DOCKER_N/A"

echo "=== Systemd ==="
timeout 3 systemctl is-active acp-bridge 2>/dev/null || echo "SYSTEMD_N/A"
```

### Diagnostic Decision Tree

Based on the output, determine the shortest path:

```
Bridge running + agents registered?
  └─ YES → Skip to Phase 5 (verify only)
  └─ NO  → Any agent CLI found? (KIRO_OK / CLAUDE_OK / ...)
              └─ YES → Skip agent install in Phase 3 (step 3.2)
                        Only install missing: runtime, config, env vars
              └─ NO  → Full install (Phase 3 all steps)
```

> **Key rule**: Never reinstall what's already working. If `CLAUDE_OK` is in the output, skip Claude install entirely. Only install agents the human explicitly requests AND are not already present.

---

## Phase 2: Collect Human Input (👤 ask once)

Based on diagnostic results, ask the human for **only** what you cannot determine yourself. Collect everything in one prompt.

| What | Ask If | Example |
|---|---|---|
| `ACP_BRIDGE_TOKEN` | `NOT_SET` | Any string, e.g. `my-secret-token` |
| `OPENCLAW_TOKEN` | `NOT_SET` and human wants webhook/Discord push | OpenClaw gateway token |
| `LITELLM_API_KEY` | `NOT_SET` and Codex/Qwen agent is needed | LiteLLM proxy token |
| `CLAUDE_CODE_USE_BEDROCK` | Claude agent needed on Bedrock | `1` |
| `ANTHROPIC_MODEL` | Claude agent needed on Bedrock | e.g. `us.anthropic.claude-sonnet-4-20250514` |
| Which agents to enable | Always (list what's found vs what's available) | `claude` / `kiro` / `codex` / `qwen` / `opencode` / `hermes` |
| Startup method | Always (unless already running) | Docker / systemd / nohup |

> If all env vars are set and service is running → skip to Phase 5.

---

## Phase 3: Install & Configure (🤖 auto)

Execute each step. **Skip if diagnostic shows it's already done.**

| Step | Action | Skip If |
|---|---|---|
| 3.1 | Install Python ≥3.12 + uv | Both found |
| 3.2 | Install CLI agent(s) — see table below | Agent already found (`*_OK`) |
| 3.3 | Clone repo + `uv sync` | `config.yaml` exists (already cloned) |
| 3.4 | Generate `config.yaml` | `CONFIG_EXISTS` |
| 3.5 | Set env vars from Phase 2 answers | All `=set` |

### Agent Install Reference

| Agent | Mode | Install |
|---|---|---|
| Kiro CLI | ACP | `curl -fsSL https://cli.kiro.dev/install \| bash` → `kiro-cli login` |
| Claude Code | ACP | `npm i -g @agentclientprotocol/claude-agent-acp` |
| Codex | PTY | `npm i -g @openai/codex` (needs LiteLLM, see [README](README.md#codex--litellm-setup)) |
| Qwen Code | ACP | `npm i -g @anthropic-ai/qwen-code` |
| OpenCode | ACP | See [opencode-ai/opencode](https://github.com/opencode-ai/opencode) |
| Hermes Agent | ACP | `pip install hermes-agent && pip install -e '.[acp]'` |

> ⚠️ `@zed-industries/claude-agent-acp` is deprecated. Use `@agentclientprotocol/claude-agent-acp`.

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
cat > /etc/systemd/system/acp-bridge.service << 'EOF'
[Unit]
Description=ACP Bridge
After=network.target
[Service]
Type=simple
WorkingDirectory=/opt/acp-bridge
Environment=ACP_BRIDGE_TOKEN=<YOUR_TOKEN>
Environment=OPENCLAW_TOKEN=<YOUR_OPENCLAW_TOKEN>
Environment=CLAUDE_CODE_USE_BEDROCK=1
Environment=ANTHROPIC_MODEL=us.anthropic.claude-sonnet-4-20250514
Environment=PATH=/root/.local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin
ExecStart=/root/.local/bin/uv run main.py --ui
Restart=on-failure
RestartSec=5
[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now acp-bridge
```

> ⚠️ `PATH` must include `/root/.local/bin` (where `uv` is installed). Missing this causes `ExecStart` to fail silently.

### nohup

```bash
nohup uv run main.py > /tmp/acp-bridge.log 2>&1 &
echo $! > /tmp/acp-bridge.pid
```

---

## Phase 5: Verify (🤖 auto)

### 5.1 Health + Agent List

```bash
# Health
curl -s --max-time 3 http://127.0.0.1:18010/health
# → {"status":"ok","version":"0.9.2"}

# Agents
curl -s http://127.0.0.1:18010/agents -H "Authorization: Bearer $ACP_BRIDGE_TOKEN"
```

### 5.2 End-to-End Agent Call

> ⚠️ **Cold start**: The first call to any agent spawns an ACP subprocess. Expect 5–15 seconds on first invocation. Set `--max-time 120` to avoid false timeout failures.

```bash
curl -s --max-time 120 -X POST http://127.0.0.1:18010/runs \
  -H "Authorization: Bearer $ACP_BRIDGE_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "agent_name": "claude",
    "input": [{
      "parts": [{
        "content": "Say hello in one sentence",
        "content_type": "text/plain"
      }]
    }]
  }'
```

> ⚠️ The ACP protocol uses `input` with a `parts` array — NOT `prompt`. Using `{"prompt":"..."}` will return `invalid_input: Field required`.

Expected response:

```json
{
  "status": "completed",
  "output": [{"parts": [{"content": "Hello! I'm Claude, ready to help..."}]}]
}
```

### 5.3 Full Test Suite (optional)

```bash
ACP_TOKEN=$ACP_BRIDGE_TOKEN bash test/test.sh http://127.0.0.1:18010
```

Report ✅/❌ to human. Done.

---

## Phase 6: Remote Access (🤖 auto if needed)

If the bridge runs on a remote machine (EC2, cloud VM), set up a tunnel for local access.

### SSH Local Forward (access remote bridge from local)

```bash
ssh -i ~/.ssh/<KEY> -L 18010:127.0.0.1:18010 <USER>@<REMOTE_IP> -N
```

Then access locally:
- Health: `curl http://127.0.0.1:18010/health`
- Web UI: http://127.0.0.1:18010/ui

### SSH Reverse Tunnel (expose local bridge to remote)

```bash
ssh -i ~/.ssh/<KEY> \
    -o StrictHostKeyChecking=no \
    -o ServerAliveInterval=30 \
    -R 18010:127.0.0.1:18010 \
    <USER>@<REMOTE_IP> -N
```

Cleanup stale listeners on remote: `fuser -k 18010/tcp`

> **No SSH key?** If the machine was provisioned without a key pair (e.g. SSM-only), inject your public key first:
> ```bash
> # Via SSM or cloud-init
> echo '<YOUR_PUBLIC_KEY>' >> /home/ec2-user/.ssh/authorized_keys
> chmod 600 /home/ec2-user/.ssh/authorized_keys
> ```

---

## Reference

### API Quick Reference

| Method | Path | Auth | Purpose |
|---|---|---|---|
| POST | `/files` | Yes | Upload a file |
| GET | `/files` | Yes | List uploaded files |
| DELETE | `/files/{filename}` | Yes | Delete an uploaded file |
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
| `skill/scripts/acp-client.sh` | Client usage examples |
| `test/test.sh` | Full test suite |

### Troubleshooting

| Symptom | Fix |
|---|---|
| `403` | Add IP to `security.allowed_ips` |
| `401` | Check `ACP_BRIDGE_TOKEN` env var |
| `pool_exhausted` | Increase `pool.max_processes` |
| `invalid_input: Field required` | Use `input` with `parts` array, not `prompt` |
| First call slow (5-15s) | Normal — ACP subprocess cold start. Subsequent calls are fast |
| Claude hangs | Already handled (auto-allow) |
| `Permission denied (publickey)` | Inject SSH public key via SSM or cloud-init |
| Codex: not trusted dir | Add `--skip-git-repo-check` to args |
| Codex: missing LITELLM_API_KEY | Set `litellm.env.LITELLM_API_KEY` in config |
| Codex: unsupported params | Set `drop_params: true` in LiteLLM config |
| Job stuck >10min | Auto-marked failed by patrol |
| Docker: env vars not passed | Use `.env` file, not shell export with `sudo` |
| systemd: env vars missing | Add `Environment=` lines to `.service` file |
| systemd: `uv` not found | Ensure `PATH` includes `/root/.local/bin` in `.service` file |
