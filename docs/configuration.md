[← Tutorial](tutorial.md) | [Agents →](agents.md)

> **Docs:** [Getting Started](getting-started.md) · [Tutorial](tutorial.md) · [Configuration](configuration.md) · [Agents](agents.md) · [API Reference](api-reference.md) · [Pipelines](pipelines.md) · [Async Jobs](async-jobs.md) · [Webhooks](webhooks.md) · [Client Usage](client-usage.md) · [Tools Proxy](tools-proxy.md) · [Security](security.md) · [Process Pool](process-pool.md) · [Testing](testing.md) · [Troubleshooting](troubleshooting.md)

# Configuration

ACP Bridge is configured via `config.yaml` in the project root. All fields are optional — Bridge auto-detects agents and generates sensible defaults when no config file is present.

## Full Reference

```yaml
server:
  host: "0.0.0.0"                              # bind address
  port: 18010                                   # listen port
  session_ttl_hours: 24                         # idle session cleanup after N hours
  shutdown_timeout: 30                          # graceful shutdown wait (seconds)
  ui: false                                     # enable Web UI at /ui (or use --ui flag)
  upload_dir: "/tmp/acp-uploads"                # file upload storage directory

pool:
  max_processes: 8                              # max total ACP subprocesses
  max_per_agent: 4                              # max subprocesses per agent type
  memory_limit_percent: 80                      # OOM eviction threshold (system memory %)

webhook:
  url: "http://<openclaw-ip>:18789/tools/invoke"  # callback endpoint
  token: "${OPENCLAW_TOKEN}"                    # Bearer auth (openclaw)
  secret: ""                                    # HMAC-SHA256 signing secret (hermes)
  format: "openclaw"                            # "openclaw" (default) or "generic"
  account_id: "default"                         # bot account identifier
  target: "channel:<default-channel-id>"        # default push target

security:
  auth_token: "${ACP_BRIDGE_TOKEN}"             # Bearer token (supports env var refs)
  allowed_ips:                                  # IP allowlist
    - "127.0.0.1"

litellm:
  url: "http://localhost:4000"                  # LiteLLM proxy URL
  required_by: ["codex", "qwen"]               # agents that need LiteLLM
  env:
    LITELLM_API_KEY: "${LITELLM_API_KEY}"       # proxy auth key

harness:
  binary: ""                                    # path to harness-factory; empty = use PATH

agents:
  kiro:
    enabled: true
    mode: "acp"
    command: "kiro-cli"
    acp_args: ["acp", "--trust-all-tools"]
    working_dir: "/tmp"
    description: "Kiro CLI agent"
  claude:
    enabled: true
    mode: "acp"
    command: "claude-agent-acp"
    acp_args: []
    working_dir: "/tmp"
    description: "Claude Code agent (via ACP adapter)"
  codex:
    enabled: true
    mode: "pty"
    command: "codex"
    args: ["exec", "--full-auto", "--skip-git-repo-check"]
    working_dir: "/tmp"
    description: "OpenAI Codex CLI agent"
  qwen:
    enabled: true
    mode: "acp"
    command: "qwen"
    acp_args: ["--acp"]
    working_dir: "/tmp"
    description: "Qwen Code agent"
  opencode:
    enabled: true
    mode: "acp"
    command: "opencode"
    acp_args: ["acp"]
    working_dir: "/tmp"
    description: "OpenCode agent (open source, multi-provider)"
  hermes:
    enabled: true
    mode: "acp"
    command: "hermes"
    acp_args: ["acp"]
    working_dir: "/tmp"
    description: "Hermes Agent (Nous Research)"
  harness:
    enabled: true
    mode: "acp"
    command: "harness-factory"
    acp_args: []
    working_dir: "/tmp"
    description: "Harness Factory lite agent (profile-driven)"
    profile:
      tools:
        fs: { permissions: [read, list] }
        git: { permissions: [diff, log, show] }
        shell: { allowlist: [pytest, mypy, grep] }
      orchestration: free
      resources:
        timeout: 300s
        max_turns: 20
      agent:
        model: "auto"
        system_prompt: "You are a code reviewer."
        temperature: 0.3
```

## Agent Configuration

Each agent entry supports these fields:

| Field | Required | Description |
|-------|----------|-------------|
| `enabled` | Yes | `true` to register this agent |
| `mode` | Yes | `"acp"` (JSON-RPC over stdio) or `"pty"` (subprocess stdout) |
| `command` | Yes | CLI binary name or path |
| `acp_args` | ACP only | Arguments to start ACP mode (e.g. `["acp", "--trust-all-tools"]`) |
| `args` | PTY only | Arguments for PTY execution (e.g. `["exec", "--full-auto"]`) |
| `working_dir` | No | Working directory for the agent subprocess (default: `/tmp`) |
| `description` | No | Human-readable description shown in `/agents` |
| `profile` | No | Harness Factory profile (tools, model, system prompt) |

## Environment Variable References

Config values support `${ENV_VAR}` syntax for secrets:

```yaml
security:
  auth_token: "${ACP_BRIDGE_TOKEN}"    # resolved from environment at startup
webhook:
  token: "${OPENCLAW_TOKEN}"
  secret: "${HERMES_WEBHOOK_SECRET}"
```

This keeps secrets out of the config file. Set them in your shell, `.env` file, or systemd `Environment=` directives.

## Webhook Formats

Bridge supports two webhook callback formats for async job results:

| Format | Target | Payload |
|--------|--------|---------|
| `openclaw` (default) | OpenClaw Gateway `/tools/invoke` | `{"tool":"message","action":"send","args":{...}}` + OpenClaw headers |
| `generic` | Any HTTP endpoint (Hermes, custom) | `{"message":"...","agent":"...","status":"...","job_id":"..."}` |

## Webhook Authentication

Bridge supports two authentication methods for webhook delivery, configured per target:

| Method | Header | Config field | Used by |
|--------|--------|-------------|---------|
| Bearer token | `Authorization: Bearer <token>` | `token` | OpenClaw |
| HMAC-SHA256 | `X-Webhook-Signature: <hex>` | `secret` | Hermes |

Set `token` or `secret` (not both). If neither is set, no auth header is sent.

For full details — payload examples, HMAC verification code, per-request overrides, chunking, retry behavior, and Hermes side setup — see [Webhooks](webhooks.md).

### Configuration examples

See [Webhooks](webhooks.md) for OpenClaw and Hermes configuration examples, payload formats, and Hermes side setup.

## Codex + LiteLLM Setup

[OpenAI Codex CLI](https://github.com/openai/codex) doesn't support ACP protocol natively, so it runs in PTY mode. To use non-OpenAI models (e.g. Kimi K2.5 on Bedrock), Codex needs [LiteLLM](https://github.com/BerriAI/litellm) as an OpenAI-compatible proxy.

### Install

```bash
npm i -g @openai/codex          # Codex CLI
pip install 'litellm[proxy]'    # LiteLLM proxy
```

### Configure Codex

```toml
# ~/.codex/config.toml
model = "bedrock/moonshotai.kimi-k2.5"
model_provider = "bedrock"

[model_providers.bedrock]
name = "AWS Bedrock via LiteLLM"
base_url = "http://localhost:4000/v1"
env_key = "LITELLM_API_KEY"
```

### Configure LiteLLM

```yaml
# ~/.codex/litellm-config.yaml
model_list:
  - model_name: "bedrock/moonshotai.kimi-k2.5"
    litellm_params:
      model: "bedrock/moonshotai.kimi-k2.5"
      aws_region_name: "us-east-1"

general_settings:
  master_key: "sk-litellm-bedrock"

litellm_settings:
  drop_params: true    # required — Codex sends params Bedrock doesn't support
```

LiteLLM uses the EC2 instance's AWS credentials (IAM Role or `~/.aws/credentials`) to access Bedrock. The `master_key` is just the proxy's own auth token.

### Start LiteLLM

```bash
LITELLM_API_KEY="sk-litellm-bedrock" litellm --config ~/.codex/litellm-config.yaml --port 4000
```

### Data Flow

```
ACP Bridge ──(PTY)──► codex exec ──(HTTP)──► LiteLLM :4000 ──(Bedrock API)──► Kimi K2.5
```

## Harness Factory Profiles

[Harness Factory](https://github.com/xiwan/harness-factory) agents are configured via `profile` in the agent entry. The profile controls tool permissions, model selection, and system prompt.

Available presets: `reader`, `executor`, `scout`, `reviewer`, `analyst`, `researcher`, `developer`, `writer`, `operator`, `admin`.

Dynamic harness agents can also be created at runtime via `POST /harness` — see [API Reference](api-reference.md).

## See Also

- [Getting Started](getting-started.md) — installation and first run
- [Agents](agents.md) — per-agent install commands and compatibility
- [Security](security.md) — auth model and deployment recommendations
