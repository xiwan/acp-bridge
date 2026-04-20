[← Async Jobs](async-jobs.md) | [Tools Proxy →](tools-proxy.md)

> **Docs:** [Getting Started](getting-started.md) · [Tutorial](tutorial.md) · [Configuration](configuration.md) · [Agents](agents.md) · [API Reference](api-reference.md) · [Pipelines](pipelines.md) · [Async Jobs](async-jobs.md) · [Client Usage](client-usage.md) · [Tools Proxy](tools-proxy.md) · [Security](security.md) · [Process Pool](process-pool.md) · [Testing](testing.md) · [Troubleshooting](troubleshooting.md)

# Client Usage

## acp-client.sh

Bash client for ACP Bridge. Requires `curl`, `jq`, `uuidgen`.

### Setup

```bash
export ACP_BRIDGE_URL=http://localhost:18010
export ACP_TOKEN=$ACP_BRIDGE_TOKEN
```

### Commands

```bash
# List agents
./skill/scripts/acp-client.sh -l

# Sync call
./skill/scripts/acp-client.sh "Explain the project structure"

# Specify agent (-a or alias: ko/cc/cx/qw/oc/hm/hf)
./skill/scripts/acp-client.sh -a kiro "Review this code"

# Streaming (SSE)
./skill/scripts/acp-client.sh --stream "Analyze this code"

# Markdown card (for IM display)
./skill/scripts/acp-client.sh --card -a kiro "Introduce yourself"

# Upload a file
./skill/scripts/acp-client.sh --upload data.csv

# Multi-turn (reuse session)
./skill/scripts/acp-client.sh -s <session_id> "continue"
```

### Agent Aliases

| Alias | Agent | Alias | Agent |
|-------|-------|-------|-------|
| `ko` | kiro | `oc` | opencode |
| `cc` | claude | `hm` | hermes |
| `cx` | codex | `hf` | harness |
| `qw` | qwen | | |

### Request Tracing

```bash
export ACP_TRACE_ID="my-trace-123"
./skill/scripts/acp-client.sh -a kiro "Hello"
# X-Request-Id: my-trace-123 in Bridge logs + response headers
```

### Options

| Flag | Description |
|------|-------------|
| `-l` | List agents |
| `-a <agent>` | Agent name or alias |
| `-s <session_id>` | Reuse session for multi-turn |
| `--stream` | SSE streaming output |
| `--card` | Markdown card format |
| `--upload <file>` | Upload a file |

## See Also

- [API Reference](api-reference.md) — HTTP endpoints
- [Async Jobs](async-jobs.md) — background tasks
- [Pipelines](pipelines.md) — multi-agent orchestration
