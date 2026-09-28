[← Agents](agents.md) | [Pipelines →](pipelines.md)

> **Docs:** [Getting Started](getting-started.md) · [Tutorial](tutorial.md) · [Configuration](configuration.md) · [Agents](agents.md) · [API Reference](api-reference.md) · [Pipelines](pipelines.md) · [Async Jobs](async-jobs.md) · [Webhooks](webhooks.md) · [Client Usage](client-usage.md) · [Tools Proxy](tools-proxy.md) · [Security](security.md) · [Process Pool](process-pool.md) · [Lambda Burst](lambda-burst.md) · [Jev Router](jev-router.md) · [Testing](testing.md) · [Troubleshooting](troubleshooting.md)

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
| `callback_url` | string | No | Override the configured webhook URL for this job. Validated against loopback/private/metadata targets (SSRF guard, see [Security → SSRF Protection](security.md#ssrf-protection)) — an unsafe value returns `400` before the job is created |

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
| `input` | string | No | Fills `{{input}}`; presence enables template rendering |
| `vars` | object | No | Extra variables, e.g. `{"bucket": "my-bucket"}` |
| `steps[].artifact` | object | No | Expected output, e.g. `{"type": "file", "label": "GDD", "pattern": "gdd-{{uid}}.md"}` |
| `context.next.steps[].artifact` | object | No | Same, for chained downstream pipelines |

#### Template rendering

When `input` or `vars` is present, the Bridge recursively substitutes `{{var}}`
in every string under `steps` and `context` before execution — so a UI can post
a template plus variables instead of pre-rendering client-side. Omit both and
the payload is passed through untouched (backward compatible).

Scope, lowest to highest precedence:

| Variable | Source |
|----------|--------|
| `{{uid}}` | Auto — 8 hex chars, unique per submission, identical everywhere in the payload |
| `{{date}}` | Auto — `YYYY-MM-DD` |
| `{{input}}` | The `input` field |
| anything else | `vars` (may also override `uid` / `date` / `input`) |

Rendering is recursive, so nested payloads such as `context.next.steps[].prompt`
are handled too.

**Unresolved variables return 400.** Unlike `POST /prompts/render`, which leaves
unknown placeholders in place, a leftover `{{bucket}}` here would make the agent
really run `aws s3 cp ... s3://{{bucket}}/` and create a literal path. The error
names each variable and its JSON path:

```json
{"error": "unresolved variables: {{bucket}} at steps[0].prompt"}
```

#### Artifacts

`steps[].artifact` declares what a step is expected to produce. The `pattern` is
rendered like any other string, then stripped from the step before execution.
`GET /pipelines/{id}` reports each declaration with resolution status — `type:
file` patterns are globbed against the pipeline's `shared_cwd`.

Declarations inside `context.next.steps[]` are handled too. Because a `next`
block is executed as a **separate downstream pipeline** with its own id, those
declarations are attached to that child pipeline rather than this one: the
submit response reports only a `chained_artifacts` count, and the resolved
array shows up on `GET /pipelines/{child_id}` (whose id appears as
`next_pipeline_id` on the parent once the chain fires). The child inherits the
parent's `uid` and `shared_cwd`, so patterns containing `{{uid}}` resolve
against the same workspace. Nested `next.next...` chains are handled at any
depth.

```bash
curl -X POST http://localhost:18010/pipelines \
  -H "Authorization: Bearer $ACP_BRIDGE_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "mode": "sequence",
    "input": "做个赛车游戏",
    "vars": {"bucket": "opengame-demo-summit-2026"},
    "context": {"shared_cwd": "/tmp/opengame"},
    "steps": [
      {"agent": "harness", "prompt": "想法：{{input}}，写入 gdd-{{uid}}.md",
       "artifact": {"type": "file", "label": "GDD", "pattern": "gdd-{{uid}}.md"}},
      {"agent": "harness", "prompt": "部署到 s3://{{bucket}}/{{uid}}/"}
    ]
  }'
```

Response echoes the generated `uid` and the rendered declarations:

```json
{"pipeline_id": "...", "status": "pending", "mode": "sequence", "steps": 2,
 "uid": "3f9a1c02",
 "artifacts": [{"type": "file", "label": "GDD", "pattern": "gdd-3f9a1c02.md"}]}
```

### `GET /pipelines`

List all pipelines.

### `GET /pipelines/{id}`

Query a single pipeline by ID. When the pipeline was submitted with template
rendering, the response also carries `uid` and a resolved `artifacts` array:

```json
{"artifacts": [{"step": 0, "agent": "harness", "type": "file", "label": "GDD",
                "pattern": "gdd-3f9a1c02.md", "exists": true,
                "path": "/tmp/opengame/gdd-3f9a1c02.md"}]}
```

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

## Lambda Pool

Serverless burst backend (v0.45.0). All endpoints return 503 unless `lambda_pool.enabled: true`. See [Lambda Burst](lambda-burst.md).

### `GET /lambda-pool/status`

Function name, region, `max_concurrent`, in-flight count, cumulative invocations and errors.

### `POST /lambda-pool/invoke`

Run one invocation. Returns 200 when the agent completed, 502 otherwise.

```bash
curl -X POST http://localhost:18010/lambda-pool/invoke \
  -H "Authorization: Bearer $ACP_BRIDGE_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"prompt":"summarize this changelog"}'
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `prompt` | string | Yes | Task prompt |
| `profile` | object | No | harness-factory profile (tools, resources, agent) |
| `model` | string | No | Overrides `lambda_pool.default_model` |
| `timeout` | int | No | Per-invocation seconds; defaults to `lambda_pool.timeout` |

### `POST /lambda-pool/invoke-batch`

Fan out N prompts in parallel. Body takes `prompts` (array of `{prompt, session_id?}`) plus optional shared `profile` and `model`.

Always returns one result per input, positionally aligned, with `total`/`completed`/`failed` counts. A per-item failure — including an at-capacity rejection — appears as a `status: "error"` entry for that item and never discards successful results.

### `POST /lambda-pool/scale`

Pre-warm containers with lightweight `__warmup__` pings. Body: `{"count": N}` (clamped to `max_concurrent`). Returns `{warmed, failed, duration}`. Best-effort: it reduces cold starts ahead of a known burst but guarantees nothing about which containers survive.

### `POST /lambda-pool/drain`

Wait for in-flight invocations to finish. Returns `{drained, remaining}`.

## Router (Jev)

Optional — see [Jev Router](jev-router.md). Both endpoints return `503 {"enabled": false}` when `router.enabled` is false. The virtual agent itself (default name `auto`) is used through the normal `POST /runs` / `POST /jobs` with `agent_name: "auto"`; its first output part is a `route_info` message naming the agent that actually ran.

### `POST /route/preview`

Dry-run: ask Jev which agent would run this prompt, without executing anything.

```bash
curl -s -X POST http://127.0.0.1:18010/route/preview \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"prompt": "Make a small HTML5 endless-runner game"}'
# {"agent":"opengame","reason":"jev","fallback":false,"jev_choice":"opengame",
#  "confidence":0.96,"probabilities":{"opengame":0.96,"qwen":0.04,"trae":0.0},
#  "model":"jev-1.13.0","latency_ms":64,"input_tokens":1057,"error":"","router":"auto"}
```

`reason`: `jev` | `low_confidence` | `other` | `invalid_choice` | `jev_error:<timeout|connection|rate_limit|http_NNN|bad_response>`. Errors: `400` missing/empty prompt or invalid JSON, `413` prompt over 20,000 chars.

### `GET /route/status`

Router config (never the API key) and counters: `decisions`, `routed`, `fallback`, `jev_errors`, `by_agent`, `by_reason`, `input_tokens`.

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

## A2A Mesh

Mesh endpoints are registered only when `mesh.enabled: true`. See [A2A Mesh](mesh.md) for setup details.

### `GET /.well-known/agent.json`

Public A2A Agent Card. No Bearer token is required.

```bash
curl -s http://localhost:18010/.well-known/agent.json
```

### `POST /a2a/announce`

Peer discovery endpoint. Uses `mesh.token`, not the global Bridge token.

```bash
curl -s -X POST http://localhost:18010/a2a/announce \
  -H "Authorization: Bearer $MESH_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"agent_card":{"url":"http://peer:18010","skills":[]},"peers":[]}'
```

### `GET /a2a/peers`

Peer table for debugging. Requires the global Bridge token.

```bash
curl -s http://localhost:18010/a2a/peers \
  -H "Authorization: Bearer $ACP_BRIDGE_TOKEN"
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

### `GET /live` (no auth)

Lightweight liveness probe. Returns HTTP 200 when the Bridge HTTP process is running.

### `GET /ready` (no auth)

Readiness probe. Returns HTTP 200 when at least one agent is configured. A lazy ACP process pool may still be `cold`; agents are spawned on the first request.

### `GET /health` (no auth)

Detailed three-state health check: `ok`, `degraded`, `unhealthy`. Includes process pool watermark, system memory, uptime, and `agent_states` counts. Agent state is `cold` before its first on-demand spawn, `ready` when a process is alive, and `down` only after an explicit health failure. A cold pool returns HTTP 200.

### `GET /health/agents`

Per-agent status with high-level state (`cold`/`ready`/`down`/`on_demand`/`remote`) and per-session connection state (`idle`/`busy`/`stale`/`dead`).

### `GET /stats`

Agent call statistics: total calls, durations, tool usage by category.

### `GET /ui` (no auth)

Web UI chat interface (requires `--ui` flag or `server.ui: true`).

## LiteLLM Proxy & Usage Tracking

### `ANY /litellm/{path}` — LiteLLM Proxy

Transparent pass-through to the LiteLLM instance. Forwards any GET/POST request.

```bash
curl -s http://localhost:18010/litellm/v1/chat/completions \
  -H "Authorization: Bearer $ACP_BRIDGE_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"model":"bedrock/deepseek.v3.2","messages":[{"role":"user","content":"hi"}],"max_tokens":10}'
```

### `GET /usage` — Aggregated Usage Stats

Query token usage, cache rates, and per-model breakdown.

| Param | Default | Description |
|-------|---------|-------------|
| `hours` | `24` | Time window |
| `model` | | Filter by model name |

```bash
curl -s http://localhost:18010/usage -H "Authorization: Bearer $ACP_BRIDGE_TOKEN"
```

Response:

```json
{
  "hours": 24.0,
  "calls": 5,
  "input_tokens": 120,
  "output_tokens": 85,
  "total_tokens": 205,
  "cached_tokens": 40,
  "cache_rate_pct": 33.3,
  "avg_duration_s": 0.82,
  "by_model": [
    {"model": "bedrock/deepseek.v3.2", "calls": 3, "input_tokens": 60, ...}
  ]
}
```

### `GET /usage/recent` — Recent Call Details

| Param | Default | Description |
|-------|---------|-------------|
| `limit` | `20` | Number of records |

### `POST /internal/llm-callback` (loopback only, no Bearer auth)

Receives `StandardLoggingPayload` from LiteLLM `generic_api` callback. Requests from non-loopback clients are rejected with HTTP 403. Not intended for direct use.

## Prompt Log

Every prompt actually sent to an agent (across pipelines, jobs, and heartbeats) is recorded into `data/jobs.db` for post-mortem and replay. Each record stores:

- `template` — the original user-supplied prompt (for pipeline steps: `prompt_template`; for jobs: raw input)
- `rendered` — after `{{var}}` substitution from pipeline context
- `final` — what actually reached the agent (includes `shared_workspace*.txt` hint and `get_prompt_suffix()`)
- `decorations` — list of layers applied, e.g. `["shared_workspace_zh", "prompt_suffix"]`

Default response **omits** the large prompt fields. Pass `?include=final` to retrieve them.

> **Privacy:** With `prompt_log.redact_secrets: true` (default), values matching OPERATIONS.md sensitive patterns are masked before write — see [Security](security.md).

### `GET /pipelines/{pipeline_id}/prompts`

Records for every step (and every conversation turn) in a pipeline.

| Param | Default | Description |
|-------|---------|-------------|
| `include` | (none) | Comma-separated extras: `final` to also return `template`/`rendered`/`final` |

```bash
curl -s "http://localhost:18010/pipelines/$PID/prompts?include=final" \
  -H "Authorization: Bearer $ACP_BRIDGE_TOKEN"
```

### `GET /jobs/{job_id}/prompts`

Records for a job (one per attempt, if fallback fired).

```bash
curl -s "http://localhost:18010/jobs/$JID/prompts?include=final" \
  -H "Authorization: Bearer $ACP_BRIDGE_TOKEN"
```

### `GET /admin/prompts`

Cross-cutting search across all parent types.

| Param | Default | Description |
|-------|---------|-------------|
| `parent_type` | (any) | `job` / `pipeline_step` / `heartbeat` |
| `agent` | (any) | Filter by agent name |
| `limit` | `50` | Max records (1–500) |
| `include` | (none) | `final` to include large fields |

```bash
# Latest 10 prompts sent to opengame across any path
curl -s "http://localhost:18010/admin/prompts?agent=opengame&limit=10" \
  -H "Authorization: Bearer $ACP_BRIDGE_TOKEN"
```

### `GET /admin/prompts/{record_id}`

Direct single-record lookup; returns `final` by default.

```bash
curl -s "http://localhost:18010/admin/prompts/<record_id>" \
  -H "Authorization: Bearer $ACP_BRIDGE_TOKEN"
```

### Disabling

Set `prompt_log.enabled: false` in `config.yaml` and restart. The endpoints will return `503 prompt logging disabled`.

## Request Tracing

All requests receive an `X-Request-Id` response header. Pass your own via the request header to stitch traces across services (e.g. OpenClaw → Bridge → agent logs).

## See Also

- [Client Usage](client-usage.md) — CLI client examples
- [Async Jobs](async-jobs.md) — background tasks and webhooks
- [Pipelines](pipelines.md) — multi-agent orchestration
- [Lambda Burst](lambda-burst.md) — serverless burst backend
- [Security](security.md) — authentication details
