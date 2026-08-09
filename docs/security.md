[← Tools Proxy](tools-proxy.md) | [Process Pool →](process-pool.md)

> **Docs:** [Getting Started](getting-started.md) · [Tutorial](tutorial.md) · [Configuration](configuration.md) · [Agents](agents.md) · [API Reference](api-reference.md) · [Pipelines](pipelines.md) · [Async Jobs](async-jobs.md) · [Webhooks](webhooks.md) · [Client Usage](client-usage.md) · [Tools Proxy](tools-proxy.md) · [Security](security.md) · [Process Pool](process-pool.md) · [Testing](testing.md) · [Troubleshooting](troubleshooting.md)

# Security

> **Before you expose this to the internet, read this entire page.**

## Threat Model

Agents run as full subprocesses of the Bridge host, with the Bridge user's permissions. Most agents can execute shell commands, read/write files, and reach any network the host can reach.

- **Token compromise ≈ shell compromise.** Anyone with `ACP_TOKEN` can tell an agent to `rm -rf`, exfiltrate `~/.ssh`, or hit internal services. Rotate tokens, never commit them, and scope `allowed_ips` tightly.
- **`--trust-all-tools`** auto-approves every tool call. Kiro's default config includes this flag — remove it in untrusted networks.
- **`session/request_permission`** is auto-answered with `proceed_always` so Claude doesn't hang. Same implication: anything the agent wants to do, it gets to do.
- **Prompt injection is a real vector.** Untrusted content fed to an agent (web pages, user input, log files) can hijack it into running unintended commands.

## Authentication

Bridge uses dual authentication:

1. **Bearer Token** — `Authorization: Bearer <token>` header on every request
2. **IP Allowlist** — only requests from `security.allowed_ips` are accepted

Both must pass. `/live`, `/ready`, `/health`, and `/ui` are unauthenticated (for load balancer probes and browser access). The public Agent Card and A2A routes use their documented mesh-token policy. The IP allowlist still applies to every path.

Token supports `${ENV_VAR}` references in config — keep actual values in `.env` or environment only. If `security.auth_token` resolves to an empty value, Bridge refuses to start instead of silently disabling authentication.

The same fail-closed rule applies to `mesh.token`: `/a2a` and `/a2a/announce` are exempt from the global Bearer check by design (they authenticate on the separate mesh plane instead — see [A2A Mesh → Security Model](mesh.md#security-model)), so if `mesh.enabled: true` and `mesh.token` resolves empty, Bridge refuses to start rather than leaving those two endpoints open with no authentication at all.

Verbose Bridge logging suppresses credential-bearing AWS SDK internals so temporary IAM session headers are not written to the service journal.

File and Pipeline artifact downloads require the normal Bearer token. The LiteLLM usage callback does not require the Bridge token, but accepts requests only from loopback clients (`127.0.0.0/8` or `::1`).

## Deployment Recommendations

| Shape | Fit | Config |
|-------|-----|--------|
| Localhost only | Personal / single-dev | `allowed_ips: ["127.0.0.1"]` |
| LAN + VPN | Small team inside office/tailnet | Bearer Token + IP allowlist |
| Public internet | **Not recommended** | mTLS reverse proxy + per-user tokens + audit logging (not shipped with Bridge) |

## Prompt-Injection Hygiene

- Don't pipe arbitrary web/user content directly into `/runs` without framing
- Keep `working_dir` pinned to a workspace directory, not `$HOME`
- Review agent transcripts for unexpected tool calls before trusting output
- Use Harness Factory's sandboxed presets (`reader`, `reviewer`) for untrusted input — they have restricted tool permissions

## Webhook Security

- Webhook token is configured separately from Bridge auth token
- OpenClaw format includes auth headers; generic format sends plain JSON
- Messages are auto-chunked at 1800 chars to avoid Discord API limits

## SSRF Protection

Two request fields let a caller supply a URL that Bridge itself then fetches or posts to server-side: `callback_url` on `POST /jobs` (see [Async Jobs](async-jobs.md)) and `workspace_in_url`/`workspace_out_url` on the mesh L3 workspace relay (`POST /a2a` `tasks/send`, see [A2A Mesh](mesh.md)). Both are validated by `src/url_safety.py`, immediately before Bridge connects to them — not just once at submission:

- Scheme must be `http` or `https`.
- The resolved host must not be loopback, link-local, private (RFC 1918), reserved, multicast, or a known cloud metadata endpoint (`169.254.169.254`, `metadata.google.internal`, and Alibaba Cloud's `100.100.100.200`, which falls outside the standard private/link-local ranges) — cloud metadata targets are blocked unconditionally, see below.

`validate_outbound_url` returns a `SafeTarget` pinned to the exact IP it just checked, and the actual request (`WebhookSender.send()` for job callbacks, the workspace download/upload in `mesh_a2a.py`) connects to that pinned IP — with TLS SNI set to the original hostname and a `Host` header carrying its full authority, port included — instead of letting the HTTP client re-resolve the hostname itself. This closes DNS rebinding: there is only ever one resolution per request, and it's the one that was checked. Redirects are explicitly disabled on these clients, since following a `30x` would connect to a fresh, unvalidated resolution and undo the pinning. Job callbacks are revalidated this way on every send, including webhook retries and jobs recovered from the store after a restart, not just at `POST /jobs` time — a `callback_url` that was safe when persisted but resolves unsafely later (or was never re-checked before) is blocked at send time, not just accepted from the store.

The guard applies to **client-supplied** URLs only. The server-configured `webhook.url` is trusted operator config and is exempt: pointing it at a private-address gateway (the documented OpenClaw setup) is a normal deployment, not an attack, and does not need an allowlist entry. A per-job `callback_url` that merely *differs* from the configured URL is still validated, even when a default is configured.

An invalid `callback_url` returns `400` with `{"error": "unsafe callback_url: ..."}` at submission (fail-fast; the enforced check happens again at send time regardless). An invalid workspace URL returns JSON-RPC error `-32014` before any download is attempted.

List specific trusted hosts/CIDRs in `security.allowed_private_targets` (a YAML list, empty by default) to opt them out of the loopback/link-local/private/reserved/multicast range checks — e.g. a self-hosted n8n instance that clients name explicitly as a `callback_url`. This allowlist only ever affects the private-range checks: cloud metadata hosts/IPs are always blocked, even if an allowlisted CIDR happens to cover them (e.g. `0.0.0.0/0`) — there is no configuration that permits a metadata target.

### Known limitations

- **Blocking DNS resolution.** The hostname resolution in `validate_outbound_url` is a synchronous `socket.getaddrinfo()` call with no explicit timeout. On `POST /jobs` this runs inside the async request handler; on the mesh workspace relay it joins pre-existing synchronous `httpx` calls in the same code path (up to 120s timeout each). A slow-to-resolve or non-responding hostname in a client-supplied URL can stall the single asyncio event loop for the OS resolver's timeout, delaying every other in-flight request. Not currently offloaded to a thread executor.

## Heartbeat & Environment Awareness

The heartbeat system (`heartbeat.enabled: true`) periodically pings agents with environment snapshots — who's online, who's busy, recent activity. This enables inter-agent collaboration.

### Security Considerations

- **Path leakage**: heartbeat prompts include a client script command for inter-agent communication. As of v0.18.0, only the script basename is shown (e.g. `acp-client.sh`), never the absolute path. Previously, the full path (e.g. `/home/user/projects/acp-bridge/skill/scripts/acp-client.sh`) was exposed, revealing the project location to all agents.
- **Agent visibility**: only agents with `heartbeat: true` in their config appear in heartbeat prompts. Agents without this flag (e.g. kiro) are invisible to other agents during heartbeat, preventing unwanted cross-agent interactions.
- **`--trust-all-tools` + auto-permission**: agents with `--trust-all-tools` (like kiro) combined with Bridge's auto-reply to `session/request_permission` can execute any shell command. Even with `working_dir` set to `/tmp/ko`, agents can `cd` or use absolute paths to access any file the Bridge user can access. `working_dir` is a starting directory, **not a sandbox**.
- **True isolation** requires running agents in Docker containers or Linux namespaces.

## Hardening Wishlist

Contributions welcome:

- Per-user tokens with scoped permissions
- ~~Rate limiting per token/IP~~ → basic per-agent RPM/TPM rate limiting added in v0.18.0 (see [Configuration](configuration.md))
- Audit logging (who called what, when)
- mTLS helper / reverse proxy config examples

## Prompt Log Privacy

Since v0.21.3, every prompt actually sent to an agent is persisted in the local SQLite (`data/jobs.db`, table `prompt_log`) for post-mortem and replay (see [API Reference → Prompt Log](api-reference.md#prompt-log)).

**What is stored:** the user-supplied template, the post-`{{var}}` rendered version, the fully decorated final string (including `shared_workspace*.txt` hint and `get_prompt_suffix()`), plus metadata (agent, session id, cwd, decorations applied, timestamp).

**What is *not* stored:** the agent's response, intermediate tool-call payloads, or any data outside the prompt itself.

### Default protections

- `prompt_log.redact_secrets: true` — values matching the patterns in `OPERATIONS.md` ("Sensitive Patterns" section) are masked with `***REDACTED***` before write. Covers `token=`, `api_key=`, `password=`, `secret=`, `ACP_BRIDGE_TOKEN=`, `OPENCLAW_TOKEN=`, `LITELLM_API_KEY=`, `ANTHROPIC_API_KEY=`, `AWS_SECRET_ACCESS_KEY=`, `Bearer <jwt>`, and `AKIA...` AWS access key ids.
- `prompt_log.max_size: 1048576` — per-field cap (1 MB); longer prompts get truncated with a marker.
- API responses default to summary-only — `final`/`template`/`rendered` are returned **only when `?include=final` is passed**.
- All endpoints require `Authorization: Bearer <token>` (existing middleware).

### Operator controls

| Setting | When to change |
|---------|----------------|
| `prompt_log.enabled: false` | Disable persistence entirely (e.g. regulated environments) |
| `prompt_log.redact_secrets: false` | Diagnostic-only — when you must inspect the exact original prompt and trust the SQLite file |
| `prompt_log.retention_days: 0` | Keep all records forever (default 30; cleanup is opt-in via cron) |

### Threat model addition

Treat `data/jobs.db` as containing potentially sensitive user input even with redaction on (heuristic regexes are not exhaustive). Apply filesystem permissions accordingly; do not commit the file to source control (already in `.gitignore`).

## See Also

- [Configuration](configuration.md) — token and IP allowlist setup
- [Process Pool](process-pool.md) — subprocess isolation details
- [Troubleshooting](troubleshooting.md) — auth error fixes
