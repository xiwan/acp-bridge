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

## S3 File Sharing (Optional)

Long async job outputs can be uploaded to S3 with presigned URLs instead of being split into Discord thread chunks. If S3 is not configured or unavailable, the system falls back to thread-based delivery.

```yaml
s3:
  bucket: "my-acp-bridge-bucket"       # S3 bucket name (auto-created if missing)
  prefix: "acp-bridge/files"           # key prefix for uploaded files
  presign_expires: 3600                # presigned URL expiry in seconds (default: 1h)
```

- **No config / no AWS access**: S3 is silently disabled; long outputs use thread chunks as before
- **Bucket doesn't exist**: Bridge attempts to create it at startup; skips S3 if creation fails
- **Region**: resolved from AWS profile, environment, or instance metadata — not hardcoded
- Set `ACP_S3_BUCKET` env var as an alternative to the config file

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

#### Auto Prompt Caching (Recommended)

LiteLLM can auto-inject `cache_control` checkpoints for Bedrock Claude models, reducing input costs by up to 90%. Add `cache_control_injection_points` to your model config:

```yaml
model_list:
  - model_name: "bedrock/anthropic.claude-sonnet-4-20250514-v1:0"
    litellm_params:
      model: "bedrock/anthropic.claude-sonnet-4-20250514-v1:0"
      aws_region_name: "us-west-2"
      cache_control_injection_points:
        - location: message
          role: system        # cache system prompts
        - location: message
          role: user
          index: 0            # cache first user message (often contains long context)
```

This works for all Claude models on Bedrock — no application code changes needed.

LiteLLM uses the EC2 instance's AWS credentials (IAM Role or `~/.aws/credentials`) to access Bedrock. The `master_key` is just the proxy's own auth token.

### Start LiteLLM

```bash
LITELLM_API_KEY="sk-litellm-bedrock" litellm --config ~/.codex/litellm-config.yaml --port 4000
```

### Data Flow

```
ACP Bridge ──(PTY)──► codex exec ──(HTTP)──► LiteLLM :4000 ──(Bedrock API)──► Kimi K2.5
```

### Cost Optimization: Prompt Caching + Region Selection

Claude 4.x models (Sonnet 4.6, Opus 4.6, etc.) on Bedrock are **only available via cross-region inference profiles** — there is no direct regional endpoint option. Cost optimization should focus on prompt caching and model selection instead.

**1. Auto-inject prompt caching** — LiteLLM can automatically add `cache_control` checkpoints for Bedrock Claude, reducing repeated input costs by up to 90%:

```yaml
model_list:
  - model_name: "bedrock/anthropic.claude-sonnet-4-6"
    litellm_params:
      model: "bedrock/us.anthropic.claude-sonnet-4-6"
      aws_region_name: "us-west-2"
      drop_params: true
      additional_drop_params: ["top_p"]
      cache_control_injection_points:
        - location: message
          role: system        # cache system prompts (largest, most stable)
```

**2. Use `us.` (US) profiles instead of `global.`** — US-scoped profiles route within US regions only, while `global.` profiles may route to more expensive regions. If your EC2 is in a US region, prefer `us.` prefix.

**3. Prefer Sonnet over Opus for routine tasks** — Opus costs ~5x more per token. Reserve Opus for tasks that genuinely need it (complex reasoning, long-horizon agentic work).

## Harness Factory Profiles

[Harness Factory](https://github.com/xiwan/harness-factory) agents are configured via `profile` in the agent entry. The profile controls tool permissions, model selection, and system prompt.

Available presets: `reader`, `executor`, `scout`, `reviewer`, `analyst`, `researcher`, `developer`, `writer`, `operator`, `admin`.

Dynamic harness agents can also be created at runtime via `POST /harness` — see [API Reference](api-reference.md).

## Heartbeat (Agent Environment Awareness)

The heartbeat system periodically checks agent process health via lightweight JSON-RPC pings (no LLM calls). Environment context is injected lazily into actual user requests via a stable prefix, maximizing prompt cache hits.

```yaml
heartbeat:
  enabled: true          # global switch
  interval: 30           # seconds between auto heartbeat pings (0 = disable auto-ping)
  language: "zh"         # prompt language: "en" or "zh"
  # client_script: ""    # optional, defaults to "acp-client.sh"
```

Auto heartbeat pings are **zero-cost** — they only verify the agent process is alive without sending prompts to the LLM. To send a full LLM heartbeat prompt manually, use `POST /heartbeat/{agent_name}`.

Per-agent opt-in: add `heartbeat: true` to each agent that should participate. Agents without this flag are invisible to other agents during heartbeat.

```yaml
agents:
  claude:
    enabled: true
    heartbeat: true      # participates in heartbeat
    # ...
  kiro:
    enabled: true
    # no heartbeat: true — hidden from other agents
```

The shared workspace (`server.public_workdir`, default `/tmp/acp-public`) is included in heartbeat prompts so agents know where to collaborate on files.

See [Security](security.md) for heartbeat security considerations.

## Metrics (Observability)

Bridge includes a lightweight metrics layer. When `prometheus_client` is installed, it exposes Prometheus counters/histograms/gauges. Without it, all metrics are emitted as structured JSON logs.

Tracked metrics:
- `agent_calls_total` — agent call count by agent and status
- `agent_call_duration_seconds` — call latency histogram
- `fallback_triggered_total` — fallback attempts by agent pair
- `fallback_exhausted_total` — fallback chain exhaustion count
- `circuit_breaker_state` — current CB state per agent (0=closed, 1=half_open, 2=open)
- `pool_connections` — connection count by agent and state (idle/busy)

To enable the Prometheus HTTP endpoint, install `prometheus_client` and the metrics server starts on port 9090.

## Rate Limiting (Per-Agent)

Optional per-agent rate limiting via `rate-limits.yaml` in the project root:

```yaml
agents:
  claude:
    rpm: 30              # max requests per minute
    tpm: 100000          # max tokens per minute
    fallback: "kiro"     # redirect to this agent when limit hit
  kiro:
    rpm: 60
    tpm: 200000
```

When an agent exceeds its RPM or TPM limit, the request is redirected to the configured fallback agent. If no fallback is configured or the fallback is also limited, the request is rejected with a rate-limit error.

## See Also

- [Getting Started](getting-started.md) — installation and first run
- [Agents](agents.md) — per-agent install commands and compatibility
- [Security](security.md) — auth model and deployment recommendations
