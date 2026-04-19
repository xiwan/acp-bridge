# Configuration

```yaml
server:
  host: "0.0.0.0"
  port: 18010
  session_ttl_hours: 24
  shutdown_timeout: 30
  ui: false                                     # enable Web UI at /ui (or use --ui flag)
  upload_dir: "/tmp/acp-uploads"                # file upload storage directory

pool:
  max_processes: 8
  max_per_agent: 4
  memory_limit_percent: 80

webhook:
  url: "http://<openclaw-ip>:18789/tools/invoke"
  token: "${OPENCLAW_TOKEN}"
  format: "openclaw"                            # "openclaw" (default) or "generic"
  account_id: "default"
  target: "channel:<default-channel-id>"        # also accepts feishu targets

security:
  auth_token: "${ACP_BRIDGE_TOKEN}"
  allowed_ips:
    - "127.0.0.1"

litellm:
  url: "http://localhost:4000"
  required_by: ["codex", "qwen"]
  env:
    LITELLM_API_KEY: "${LITELLM_API_KEY}"

harness:
  binary: ""                                    # absolute path to harness-factory; empty = use PATH

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
  # harness-factory: same binary, different profiles → different agents
  # name is arbitrary — use "harness", "pr-reviewer", "translator", etc.
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

## Codex + LiteLLM Setup

[OpenAI Codex CLI](https://github.com/openai/codex) doesn't support ACP protocol natively, so it runs in PTY mode (subprocess). To use non-OpenAI models (e.g. Kimi K2.5 on Bedrock), Codex needs [LiteLLM](https://github.com/BerriAI/litellm) as an OpenAI-compatible proxy.

### Install

```bash
# Codex CLI
npm i -g @openai/codex

# LiteLLM proxy
pip install 'litellm[proxy]'
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
  drop_params: true
```

`drop_params: true` is required — Codex sends parameters (e.g. `web_search_options`) that Bedrock doesn't support.

LiteLLM uses the EC2 instance's AWS credentials (IAM Role or `~/.aws/credentials`) to access Bedrock. The `master_key` is just the proxy's own auth token.

### Start LiteLLM

```bash
LITELLM_API_KEY="sk-litellm-bedrock" litellm --config ~/.codex/litellm-config.yaml --port 4000
```

### Data Flow

```
acp-bridge ──(PTY)──► codex exec ──(HTTP)──► LiteLLM :4000 ──(Bedrock API)──► Kimi K2.5
```
