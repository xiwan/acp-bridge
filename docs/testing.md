[← Process Pool](process-pool.md) | [Troubleshooting →](troubleshooting.md)

> **Docs:** [Getting Started](getting-started.md) · [Configuration](configuration.md) · [Agents](agents.md) · [API Reference](api-reference.md) · [Pipelines](pipelines.md) · [Async Jobs](async-jobs.md) · [Client Usage](client-usage.md) · [Tools Proxy](tools-proxy.md) · [Security](security.md) · [Process Pool](process-pool.md) · [Testing](testing.md) · [Troubleshooting](troubleshooting.md)

# Testing

## Agent Compliance Test

Verify a CLI agent implements the ACP protocol correctly — **no Bridge needed**. Tests the agent binary directly over stdio.

```bash
bash test/test_agent_compliance.sh kiro-cli acp --trust-all-tools
bash test/test_agent_compliance.sh claude-agent-acp
bash test/test_agent_compliance.sh python3 examples/echo-agent.py
```

### Test Cases

| ID | Test | Level |
|----|------|-------|
| T1.1 | `initialize` returns result | Required |
| T1.2 | `initialize` includes `agentInfo` | Required |
| T2.1 | `session/new` returns result | Required |
| T2.2 | `session/new` includes `sessionId` | Required |
| T3.1 | `session/prompt` sends `agent_message_chunk` notifications | Required |
| T3.2 | `session/prompt` returns final result | Required |
| T3.3 | `session/prompt` result includes `stopReason` | Required |
| T4 | `ping` returns a response | Optional |

T1–T3 all passing = **compliant** — the agent can be used in ACP mode.

See [`AGENT_SPEC.md`](../AGENT_SPEC.md) for the full protocol specification.

## Integration Tests

Run the full test suite against a running Bridge instance:

```bash
ACP_TOKEN=$ACP_BRIDGE_TOKEN bash test/test.sh http://127.0.0.1:18010
```

### Individual Agent Tests

```bash
ACP_TOKEN=$ACP_BRIDGE_TOKEN bash test/test_kiro.sh
ACP_TOKEN=$ACP_BRIDGE_TOKEN bash test/test_claude.sh
ACP_TOKEN=$ACP_BRIDGE_TOKEN bash test/test_codex.sh
ACP_TOKEN=$ACP_BRIDGE_TOKEN bash test/test_qwen.sh
ACP_TOKEN=$ACP_BRIDGE_TOKEN bash test/test_opencode.sh
ACP_TOKEN=$ACP_BRIDGE_TOKEN bash test/test_hermes.sh
```

### Filter from Main Runner

```bash
ACP_TOKEN=$ACP_BRIDGE_TOKEN bash test/test.sh http://127.0.0.1:18010 --only codex
```

### Coverage

The integration suite covers:

- Agent listing and health endpoints
- Sync and streaming calls
- Multi-turn conversation (session reuse)
- Per-agent specific tests (ACP + PTY modes)
- Async job submission and status
- Pipeline execution (all modes)
- Error handling (invalid agent, bad input, auth failures)
- OpenClaw tools proxy

## Test Infrastructure

- `test/lib.sh` — shared helpers (assertions, env init, result parsing)
- `test/scratch/` — gitignored directory for throwaway validation scripts
- Test reports are written to `test/reports/`

## See Also

- [Agents](agents.md) — agent compatibility and install
- [Troubleshooting](troubleshooting.md) — common test failures
- [Agent Spec](../AGENT_SPEC.md) — protocol specification
