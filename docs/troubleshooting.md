[← Testing](testing.md) | [README →](../README.md)

> **Docs:** [Getting Started](getting-started.md) · [Tutorial](tutorial.md) · [Configuration](configuration.md) · [Agents](agents.md) · [API Reference](api-reference.md) · [Pipelines](pipelines.md) · [Async Jobs](async-jobs.md) · [Client Usage](client-usage.md) · [Tools Proxy](tools-proxy.md) · [Security](security.md) · [Process Pool](process-pool.md) · [Testing](testing.md) · [Troubleshooting](troubleshooting.md)

# Troubleshooting

## Authentication

| Symptom | Cause | Fix |
|---------|-------|-----|
| `403 Forbidden` | IP not in allowlist | Add your IP to `security.allowed_ips` in config |
| `401 Unauthorized` | Wrong or missing token | Check `Authorization: Bearer <token>` header matches `ACP_BRIDGE_TOKEN` |

## Process Pool

| Symptom | Cause | Fix |
|---------|-------|-----|
| `pool_exhausted` | All subprocess slots busy | Increase `pool.max_processes` in config, or wait for idle sessions to free |
| First call slow (5–15s) | ACP subprocess cold start | Normal — subsequent calls to the same agent reuse the process |
| Agent hangs | Permission request not answered | Already handled (auto-allow). If still hanging, check agent logs via stderr |

## Agents

| Symptom | Cause | Fix |
|---------|-------|-----|
| Claude hangs indefinitely | Permission schema mismatch | Update `claude-agent-acp` to latest; Bridge auto-replies `allow_always` |
| `invalid_input: Field required` | Wrong request format | Use `input` with `parts` array, not `{"prompt":"..."}` |
| Agent not listed in `/agents` | Not installed or not in PATH | Install the agent CLI; or check `config.yaml` has `enabled: true` |

## Codex (PTY)

| Symptom | Cause | Fix |
|---------|-------|-----|
| `not trusted dir` | `/tmp` is not a git repo | Add `--skip-git-repo-check` to `args` in config |
| Missing `LITELLM_API_KEY` | Env var not passed to subprocess | Set `litellm.env.LITELLM_API_KEY` in config |
| Unsupported params error | Bedrock rejects Codex-specific params | Set `drop_params: true` in LiteLLM config |
| PTY agent timeout | Long task exceeds `max_duration` | Increase `max_duration` (default: 600s) or use async job |

## Harness Factory

| Symptom | Cause | Fix |
|---------|-------|-----|
| `[loop detected: fs_read]` | Preset can't write but prompt asks to save | Use `developer`/`writer` preset, or rewrite prompt to return text only |
| Instant completion with raw XML/markdown | Model incompatible with harness tool format | Specify `model: "deepseek-v3"` or `"claude-sonnet"` instead of `"auto"` |
| Harness agent not found | Dynamic harness not created | `POST /harness` first, then call the returned agent name |

## Async Jobs & Webhooks

| Symptom | Cause | Fix |
|---------|-------|-----|
| Discord push fails | Wrong or missing `account_id` | Use `"default"` for Discord, `"main"` for Feishu |
| Discord 500 error | Bad target format | DM: `user:<id>`, channel: `channel:<id>` |
| Job stuck >10min | Agent process anomaly | Auto-marked failed by patrol; check agent logs |
| Webhook delivery fails | Endpoint unreachable | Check `webhook.url` in config; retries happen automatically |

## Docker

| Symptom | Cause | Fix |
|---------|-------|-----|
| Env vars not passed | `sudo` doesn't forward shell env | Use `.env` file instead of `export` |
| Agent binary not found | Not mounted into container | Uncomment volume mounts in `docker-compose.yml` |

## systemd

| Symptom | Cause | Fix |
|---------|-------|-----|
| `uv` not found | PATH missing in service file | Add `Environment=PATH=/root/.local/bin:/usr/local/bin:...` to `.service` |
| Env vars missing | Not set in service file | Add `Environment=ACP_BRIDGE_TOKEN=...` lines |
| Service won't start | Port already in use | `fuser -k 18010/tcp` then restart |

## Pipelines

| Symptom | Cause | Fix |
|---------|-------|-----|
| Step can't read file from prior step | Prior step didn't write to shared workspace | Verify `working_dir` is shared; check prior step output |
| Conversation stops early | `max_turns` reached | Increase `max_turns` (default: 6, max: 12) |

## See Also

- [Security](security.md) — auth model
- [Process Pool](process-pool.md) — subprocess management
- [Configuration](configuration.md) — all config options
