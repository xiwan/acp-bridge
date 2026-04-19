# Security Considerations

**Before you expose this to the internet, read this.**

Agents run as full subprocesses of the Bridge host, with the Bridge user's permissions. Most agents can execute shell commands, read/write files, and reach any network the host can reach. A few consequences follow:

- **Token compromise ≈ shell compromise.** Anyone with `ACP_TOKEN` can tell an agent to `rm -rf`, exfiltrate `~/.ssh`, or hit internal services. Rotate tokens, never commit them, and scope `allowed_ips` tightly.
- **`--trust-all-tools` is a developer convenience, not a production posture.** It auto-approves every tool call. Kiro's default config includes this flag — remove it or run Bridge only in trusted networks.
- **`session/request_permission` is auto-answered with `allow_always`** so Claude doesn't hang. Same implication: anything the agent wants to do, it gets to do.
- **Prompt injection is a real vector.** Untrusted content fed to an agent (web pages, user input, log files) can hijack it into running commands you didn't intend.

## Recommended deployment shapes

| Shape | Fit |
|-------|-----|
| Localhost only (`allowed_ips: [127.0.0.1]`) | Personal/single-dev use |
| LAN + VPN + Bearer Token | Small team inside an office/tailnet |
| Public internet | **Not recommended.** If you must, put Bridge behind mTLS reverse proxy + per-user tokens + audit logging (none of which Bridge ships today) |

## Prompt-injection hygiene

- Don't pipe arbitrary web/user content directly into `/runs` without framing
- Keep `working_dir` pinned to a workspace, not `$HOME`
- Review the agent transcript for unexpected tool calls before trusting its output

Contributions that harden this (per-user tokens, rate limits, audit log, mTLS helper) are welcome.

## Authentication

- IP allowlist + Bearer Token dual authentication
- `/health` is unauthenticated (for load balancer probes)
- Token supports `${ENV_VAR}` environment variable references
- Webhook token is configured separately from Bridge auth token
