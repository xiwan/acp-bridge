# Testing

## Agent Compliance Test

Verify a CLI agent implements the ACP protocol correctly — **no Bridge required**:

```bash
bash test/test_agent_compliance.sh kiro-cli acp --trust-all-tools
bash test/test_agent_compliance.sh claude-agent-acp
bash test/test_agent_compliance.sh python3 examples/echo-agent.py
```

Covers: initialize, session/new, session/prompt (notifications + result), ping. See [AGENT_SPEC.md](../AGENT_SPEC.md) for the full specification.

## Integration Tests

```bash
ACP_TOKEN=<token> bash test/test.sh http://127.0.0.1:18010
```

Run individual agent tests:

```bash
ACP_TOKEN=<token> bash test/test_codex.sh
ACP_TOKEN=<token> bash test/test_kiro.sh
ACP_TOKEN=<token> bash test/test_claude.sh
ACP_TOKEN=<token> bash test/test_qwen.sh
```

Or filter from the main runner:

```bash
ACP_TOKEN=<token> bash test/test.sh http://127.0.0.1:18010 --only codex
```

Covers: agent listing, sync/streaming calls, multi-turn conversation, Claude, Codex, async jobs, error handling.
