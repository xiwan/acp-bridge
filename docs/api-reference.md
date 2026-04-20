[← Agents](agents.md) | [Pipelines →](pipelines.md)

> **Docs:** [Getting Started](getting-started.md) · [Tutorial](tutorial.md) · [Configuration](configuration.md) · [Agents](agents.md) · [API Reference](api-reference.md) · [Pipelines](pipelines.md) · [Async Jobs](async-jobs.md) · [Webhooks](webhooks.md) · [Client Usage](client-usage.md) · [Tools Proxy](tools-proxy.md) · [Security](security.md) · [Process Pool](process-pool.md) · [Testing](testing.md) · [Troubleshooting](troubleshooting.md)

# API Reference

All endpoints require `Authorization: Bearer <token>` unless noted otherwise.

## Agents

### `GET /agents`

List all registered agents.

```bash
curl -s http://localhost:18010/agents \
  -H "Authorization: Bearer $ACP_BRIDGE_TOKEN"
```

Response:

```json
{
  "agents": [
    {"name": "kiro", "mode": "acp", "description": "Kiro CLI agent"},
    {"name": "claude", "mode": "acp", "description": "Claude Code agent"}
  ]
}
```

## Runs

### `POST /runs`

Synchronous or streaming agent call.

> **Input format note:** The `input` field uses the [ACP protocol](https://agentclientprotocol.com/) message format (nested `parts` array). If this feels verbose, use [`acp-client.sh`](client-usage.md) which wraps it for you:
> ```bash
> ./skill/scripts/acp-client.sh -a kiro "Hello"   # no JSON needed
> ```

**Request body:**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `agent_name` | string | Yes | Agent to call |
| `input` | array | Yes | ACP input: `[{"parts": [{"content": "...", "content_type": "text/plain"}]}]` |
| `stream` | boolean | No | `true` for SSE streaming (default: `false`) |
| `session_id` | string | No | Reuse an existing session for multi-turn |
| `cwd` | string | No | Working directory override |

```bash
# Sync
curl -s -X POST http://localhost:18010/runs \
  -H "Authorization: Bearer $ACP_BRIDGE_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"agent_name":"kiro","input":[{"parts":[{"content":"Hello","content_type":"text/plain"}]}]}'

# Streaming (SSE)
curl -N -X POST http://localhost:18010/runs \
  -H "Authorization: Bearer $ACP_BRIDGE_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"agent_name":"kiro","input":[{"parts":[{"content":"Hello","content_type":"text/plain"}]}],"stream":true}'
```

> ⚠️ Use `input` with a `parts` array — NOT `prompt`. Using `{"prompt":"..."}` returns `invalid_input: Field required`.

## Jobs

### `POST /jobs`

Submit an async background job. See [Async Jobs](async-jobs.md) for full details.

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `agent_name` | string | Yes | Agent to run |
| `prompt` | string | Yes | Task prompt |
| `target` | string | No | Webhook push target (e.g. `channel:123`, `user:456`) |
| `channel` | string | No | IM channel (`discord`, `feishu`) |
| `callback_meta` | object | No | Extra webhook metadata (e.g. `{"account_id": "default"}`) |

### `GET /jobs`

List all jobs with status stats.

### `GET /jobs/{job_id}`

Query a single job by ID.

## Pipelines

### `POST /pipelines`

Submit a multi-agent pipeline. See [Pipelines](pipelines.md) for full details.

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `mode` | string | Yes | `sequence`, `parallel`, `race`, or `conversation` |
| `steps` | array | Yes | `[{"agent": "kiro", "prompt": "..."}]` |
| `max_turns` | integer | No | Conversation mode only (default: 6, max: 12) |
| `target` | string | No | Webhook push target |
| `channel` | string | No | IM channel |

### `GET /pipelines`

List all pipelines.

### `GET /pipelines/{id}`

Query a single pipeline by ID.

### `GET /stats/pipelines`

Per-mode aggregation stats. Optional `?hours=N` query param (default: 168 = 7 days).

## Harness

### `POST /harness`

Create a dynamic harness agent at runtime.

```bash
curl -X POST http://localhost:18010/harness \
  -H "Authorization: Bearer $ACP_BRIDGE_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"profile":"reviewer","system_prompt":"Review Python code for security issues"}'
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `profile` | string | Yes | Preset name (e.g. `reviewer`, `developer`, `operator`) |
| `system_prompt` | string | No | Custom system prompt |
| `model` | string | No | Model alias (default: `auto`) |

### `GET /harness`

List dynamic harness agents. Response includes `resolved_model` (populated after first call).

### `DELETE /harness/{name}`

Delete a dynamic harness agent.

## Files

### `POST /files`

Upload a file (multipart form data).

```bash
curl -X POST http://localhost:18010/files \
  -H "Authorization: Bearer $ACP_BRIDGE_TOKEN" \
  -F "file=@data.csv"
```

### `GET /files`

List uploaded files.

### `DELETE /files/{filename}`

Delete an uploaded file.

## Tools Proxy

### `GET /tools`

List available OpenClaw tools. See [Tools Proxy](tools-proxy.md).

### `POST /tools/invoke`

Invoke an OpenClaw tool.

```bash
curl -X POST http://localhost:18010/tools/invoke \
  -H "Authorization: Bearer $ACP_BRIDGE_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"tool":"message","action":"send","args":{"channel":"discord","target":"channel:123","message":"Hello"}}'
```

## Chat (Web UI)

### `POST /chat/messages`

Save a chat message.

### `GET /chat/messages`

Load recent chat messages. Optional `?session_id=` query param.

### `DELETE /chat/messages`

Clear all chat messages.

### `POST /chat/fold`

Fold (collapse) a session's messages in the UI.

## Templates

### `GET /templates`

List available prompt templates.

### `POST /templates/render`

Render a template with variables.

```bash
curl -X POST http://localhost:18010/templates/render \
  -H "Authorization: Bearer $ACP_BRIDGE_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"template":"code-review","variables":{"file":"src/agents.py"}}'
```

## Sessions

### `DELETE /sessions/{agent}/{session_id}`

Close a session and release its subprocess.

## Health & Stats

### `GET /health` (no auth)

Three-state health check: `ok`, `degraded`, `unhealthy`. Includes process pool watermark, system memory, uptime.

### `GET /health/agents`

Per-agent status with per-session connection state (`idle`/`busy`/`stale`/`dead`).

### `GET /stats`

Agent call statistics: total calls, durations, tool usage by category.

### `GET /ui` (no auth)

Web UI chat interface (requires `--ui` flag or `server.ui: true`).

## Request Tracing

All requests receive an `X-Request-Id` response header. Pass your own via the request header to stitch traces across services (e.g. OpenClaw → Bridge → agent logs).

## See Also

- [Client Usage](client-usage.md) — CLI client examples
- [Async Jobs](async-jobs.md) — background tasks and webhooks
- [Pipelines](pipelines.md) — multi-agent orchestration
- [Security](security.md) — authentication details
