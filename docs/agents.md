[← Configuration](configuration.md) | [API Reference →](api-reference.md)

> **Docs:** [Getting Started](getting-started.md) · [Tutorial](tutorial.md) · [Configuration](configuration.md) · [Agents](agents.md) · [API Reference](api-reference.md) · [Pipelines](pipelines.md) · [Async Jobs](async-jobs.md) · [Webhooks](webhooks.md) · [Client Usage](client-usage.md) · [Tools Proxy](tools-proxy.md) · [Security](security.md) · [Process Pool](process-pool.md) · [Lambda Burst](lambda-burst.md) · [Jev Router](jev-router.md) · [Testing](testing.md) · [Troubleshooting](troubleshooting.md)

# Agents

## Compatibility Matrix

| Agent | Vendor | ACP | Mode | Status | Tests | Install |
|-------|--------|-----|------|--------|-------|---------|
| [Kiro CLI](https://github.com/aws/kiro-cli) | AWS | ✅ Native | `acp` | ✅ Integrated | 7/7 | `curl -fsSL https://cli.kiro.dev/install \| bash` |
| [Claude Code](https://github.com/anthropics/claude-code) | Anthropic | ✅ Native | `acp` | ✅ Integrated | 5/5 | `npm i -g @agentclientprotocol/claude-agent-acp` |
| [Qwen Code](https://www.npmjs.com/package/@anthropic-ai/qwen-code) | Alibaba | ✅ `--acp` | `acp` | ✅ Integrated | 6/6 | `npm i -g @anthropic-ai/qwen-code` |
| [OpenGame](https://github.com/leigest519/OpenGame) | Open Source | ✅ `--acp` | `acp` | ✅ Integrated | — | See [repo](https://github.com/leigest519/OpenGame) |
| [OpenAI Codex](https://github.com/openai/codex) | OpenAI | ✅ | `acp` | ✅ Integrated | 6/6 | `npm i -g @openai/codex @agentclientprotocol/codex-acp` |
| [Gemini CLI](https://github.com/google-gemini/gemini-cli) | Google | 🧪 Experimental | — | 🟡 Planned | — | — |
| [Copilot CLI](https://docs.github.com/en/copilot/reference/acp-server) | GitHub | ✅ `--acp` | — | 🟡 Planned | — | — |
| [OpenCode](https://github.com/opencode-ai/opencode) | Open Source | ✅ `opencode acp` | `acp` | ✅ Integrated | 6/6 | See [repo](https://github.com/opencode-ai/opencode) |
| [Harness Factory](https://github.com/xiwan/harness-factory) | Open Source | ✅ Native | `acp` | ✅ Integrated | 4/4 | See [repo](https://github.com/xiwan/harness-factory) |
| [Hermes Agent](https://github.com/NousResearch/hermes-agent) | Nous Research | ✅ `hermes acp` | `acp` | ✅ Integrated | 8/8 | `uv tool install 'hermes-agent[acp] @ git+...'` |
| [AWS DevOps Agent](https://github.com/aws-samples/sample-aws-devops-agent-acp-mcp) | AWS | ✅ Native | `acp` | ✅ Optional safe launcher | 16 unit | Install reviewed sample in isolated venv |
| [OpenClaw](https://github.com/openclaw/openclaw) | Open Source | ✅ `openclaw acp` | `acp` | ✅ Integrated | — | `sudo npm i -g openclaw` |
| [CoStrict](https://github.com/zgsm-ai/costrict) | Open Source 🇨🇳 | ✅ Native | — | 🟡 Planned | — | — |
| [Trae Agent](https://github.com/bytedance/trae-agent) | ByteDance 🇨🇳 | ❌ | `pty` | ✅ Integrated | 4/4 | `cd trae-agent && uv sync --all-extras` |
| [Aider](https://github.com/Aider-AI/aider) | Open Source | ❌ | — | ⚪ No ACP | — | — |

**Legend:** ✅ Integrated — 🟡 Planned (ACP-ready) — ⚪ No ACP support yet — 🧪 Experimental

## Integration Modes

### ACP Mode (recommended)

Agents communicate via stdio JSON-RPC. Enables process reuse, multi-turn context, structured events (thinking/tool/status), and SSE streaming.

```yaml
agents:
  kiro:
    enabled: true
    mode: "acp"
    command: "kiro-cli"
    acp_args: ["acp", "--trust-all-tools"]
    working_dir: "/tmp"
```

### PTY Mode (fallback)

For agents without ACP support. Bridge spawns a new subprocess per request and reads stdout. No process reuse, no context retention, no structured events.

```yaml
agents:
  codex:
    enabled: true
    mode: "pty"
    command: "codex"
    args: ["exec", "--full-auto", "--skip-git-repo-check"]
    working_dir: "/tmp"
```

## Agent-Specific Notes

### Kiro CLI

- Login required: `kiro-cli login`
- `--trust-all-tools` auto-approves all tool calls — remove in untrusted networks

### Claude Code

- Uses `claude-agent-acp` adapter (not the `claude` CLI directly)
- `session/request_permission` is auto-answered with `proceed_always` by Bridge
- For Bedrock: set `CLAUDE_CODE_USE_BEDROCK=1` and `ANTHROPIC_MODEL=<model-id>`
- ⚠️ `@zed-industries/claude-agent-acp` is deprecated — use `@agentclientprotocol/claude-agent-acp`

### OpenAI Codex

- PTY mode only (no ACP support)
- Requires [LiteLLM](https://github.com/BerriAI/litellm) proxy for non-OpenAI models — see [Configuration](configuration.md#codex--litellm-setup)
- Add `--skip-git-repo-check` if `working_dir` is not a git repo

### Trae Agent

- PTY mode only (no ACP support)
- Requires [LiteLLM](https://github.com/BerriAI/litellm) proxy for Bedrock models — use `openrouter` provider in `trae_config.yaml`
- LiteLLM must set `additional_drop_params: ["top_p"]` on Anthropic Claude models (Bedrock rejects `temperature` + `top_p` together, and trae always sends both)
- Config file: `~/projects/trae-agent/trae_config.yaml`

### Harness Factory

- Profile-driven: same binary + different profiles = different agents
- Dynamic creation at runtime via `POST /harness`
- Built-in model registry with `"auto"` random selection and error fallback
- Presets: `reader`, `executor`, `scout`, `reviewer`, `analyst`, `researcher`, `developer`, `writer`, `operator`, `admin`
- External skills via `skills_dir` in profile config (e.g. `s3-deploy` for static file deployment)

### OpenGame

- Fork of Qwen Code, specialized for AI web game generation
- Uses `--acp` flag (same ACP protocol as Qwen Code)
- Requires `QWEN_CODE_NO_RELAUNCH=1` to prevent subprocess relaunch in non-TTY
- Requires `authenticate(openai)` call before `session/new` — Bridge handles this automatically
- Requires `fs` client capability — Bridge provides `readTextFile` and `writeTextFile`
- Generated files written to `working_dir` (default `/tmp/opengame/`)
- Works well in sequence pipeline with harness for auto-deployment to S3

### Hermes Agent

- Also serves as webhook callback target via its webhook adapter
- Configure `format: "generic"` in Bridge webhook config to use Hermes for IM delivery

### AWS DevOps Agent

AWS's [`sample-aws-devops-agent-acp-mcp`](https://github.com/aws-samples/sample-aws-devops-agent-acp-mcp)
provides a native stdio ACP server. Bridge uses it through
`src/adapters/aws_devops_launcher.py`; the shared ACP client is unchanged.

#### Install the reviewed sample

Keep this optional dependency outside the Bridge environment. The commit below
is the v1.0.0 implementation reviewed for this integration; review upstream
changes before moving the pin.

```bash
git clone https://github.com/aws-samples/sample-aws-devops-agent-acp-mcp.git /opt/aws-devops-agent
git -C /opt/aws-devops-agent checkout 6d4f1d295def858d56c4020fa20744d3ce78ee12
uv venv /opt/aws-devops-agent/.venv --python 3.12
uv pip install --python /opt/aws-devops-agent/.venv/bin/python -e /opt/aws-devops-agent
```

Configure AWS credentials for the same OS account that runs the Bridge service,
preferably with an EC2 instance role or ECS task role. Do not place access keys
in `config.yaml`. Set these non-secret selectors in `.env` or the systemd unit:

```bash
DEVOPS_AGENT_USER_ID=<operator-id>
DEVOPS_AGENT_REGION=us-east-1
DEVOPS_AGENT_SPACE_ID=<existing-agent-space-id>
```

Copy the disabled `agents.aws-devops` block from `config.yaml.example`, replace
the two `/opt/...` paths if needed, and set `enabled: true`. Safe defaults are:

- a fixed `DEVOPS_AGENT_USER_ID` and `DEVOPS_AGENT_SPACE_ID` are required;
- AgentSpace auto-creation is blocked;
- ordinary incident language remains a normal chat request;
- only `/investigate <incident description>` starts a deep investigation.

Launcher controls:

| Variable | Default | Meaning |
|----------|---------|---------|
| `ACP_BRIDGE_AWS_INVESTIGATION_MODE` | `explicit` | `disabled`, `explicit`, or upstream-compatible `auto` |
| `ACP_BRIDGE_AWS_REQUIRE_SPACE_ID` | `true` | Set `false` only to allow upstream read-only AgentSpace discovery |
| `ACP_BRIDGE_AWS_ALLOW_SPACE_CREATE` | `false` | Must be `true` together with `DEVOPS_AGENT_AUTO_CREATE_SPACE=true` to create a space |

Call normal chat like any other Bridge agent. Start investigation only when
intended:

```text
agent: aws-devops
prompt: /investigate root cause of checkout-service 503 errors
```

The upstream server returns the initial chat response and investigation task ID,
then emits journal updates in the background. Bridge v0.36.0 intentionally does
not change its request-scoped ACP event lifecycle, so later journal notifications
are not guaranteed to appear in the completed `/runs` response. Use the task ID
for operational follow-up. Also avoid ACP `resume_session_id`: the sample does
not implement `session/load`.

Before production use, follow the upstream onboarding guide for the caller role
and AgentSpace service role, enable CloudTrail, and validate in a non-production
account. Investigation can consume API quota and incur charges.

## Zero-Config Auto-Detection

When no `config.yaml` is present, Bridge scans `PATH` for known agent CLIs and registers them with default settings. Supported: `kiro-cli`, `claude-agent-acp`, `codex`, `trae-cli`, `qwen`, `opencode`, `hermes`, `harness-factory`. AWS DevOps Agent is deliberately excluded from zero-config discovery because its identity, AgentSpace, and investigation policy must be explicit.

## Writing a New Agent

Implement three JSON-RPC methods over stdio:

1. **`initialize`** — handshake, return agent info
2. **`session/new`** — create a conversation session
3. **`session/prompt`** — receive prompt, stream notifications, return result

See [`AGENT_SPEC.md`](../AGENT_SPEC.md) for the full protocol and [`examples/echo-agent.py`](../examples/echo-agent.py) for a minimal reference.

```bash
bash test/test_agent_compliance.sh my-agent-cli [args...]
```

## See Also

- [Configuration](configuration.md) — `config.yaml` reference
- [Process Pool](process-pool.md) — subprocess lifecycle management
- [Testing](testing.md) — compliance and integration tests
