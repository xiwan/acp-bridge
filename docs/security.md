[← Tools Proxy](tools-proxy.md) | [Process Pool →](process-pool.md)

> **Docs:** [Getting Started](getting-started.md) · [Tutorial](tutorial.md) · [Configuration](configuration.md) · [Agents](agents.md) · [API Reference](api-reference.md) · [Pipelines](pipelines.md) · [Async Jobs](async-jobs.md) · [Client Usage](client-usage.md) · [Tools Proxy](tools-proxy.md) · [Security](security.md) · [Process Pool](process-pool.md) · [Testing](testing.md) · [Troubleshooting](troubleshooting.md)

# Security

> **Before you expose this to the internet, read this entire page.**

## Threat Model

Agents run as full subprocesses of the Bridge host, with the Bridge user's permissions. Most agents can execute shell commands, read/write files, and reach any network the host can reach.

- **Token compromise ≈ shell compromise.** Anyone with `ACP_TOKEN` can tell an agent to `rm -rf`, exfiltrate `~/.ssh`, or hit internal services. Rotate tokens, never commit them, and scope `allowed_ips` tightly.
- **`--trust-all-tools`** auto-approves every tool call. Kiro's default config includes this flag — remove it in untrusted networks.
- **`session/request_permission`** is auto-answered with `allow_always` so Claude doesn't hang. Same implication: anything the agent wants to do, it gets to do.
- **Prompt injection is a real vector.** Untrusted content fed to an agent (web pages, user input, log files) can hijack it into running unintended commands.

## Authentication

Bridge uses dual authentication:

1. **Bearer Token** — `Authorization: Bearer <token>` header on every request
2. **IP Allowlist** — only requests from `security.allowed_ips` are accepted

Both must pass. `/health` and `/ui` are unauthenticated (for load balancer probes and browser access).

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

## Hardening Wishlist

Contributions welcome:

- Per-user tokens with scoped permissions
- Rate limiting per token/IP
- Audit logging (who called what, when)
- mTLS helper / reverse proxy config examples

## See Also

- [Configuration](configuration.md) — token and IP allowlist setup
- [Process Pool](process-pool.md) — subprocess isolation details
- [Troubleshooting](troubleshooting.md) — auth error fixes
