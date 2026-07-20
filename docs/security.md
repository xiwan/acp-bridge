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

Both must pass. `/live`, `/ready`, `/health`, and `/ui` are unauthenticated (for load balancer probes and browser access). The IP allowlist still applies to these paths.

Token supports `${ENV_VAR}` references in config — keep actual values in `.env` or environment only.

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
