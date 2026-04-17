# ACP Bridge — Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| `❌ Connection failed` | Service not running or wrong address | Verify Bridge address and port |
| `unauthorized` | Incorrect token | Ask user to confirm token (do not echo it) |
| `forbidden` | IP not in allowlist | Contact Bridge admin to add IP |
| `422 Unprocessable` | session_id is not UUID format | Use UUID-format session_id |
| `❌ server_error` | CLI execution error | Check error message for details |
| Long timeout | CLI processing or session conflict | Ensure different agents use different session_ids |
| Discord push fails | Wrong `account_id` | Use `default` for Discord, `main` for Feishu |
| Discord 500 | Bad target format | DM: `user:<id>`, channel: `channel:<id>` |
| Claude output empty / `tool_call (failed)` / `User refused permission to run tool` | Bridge pre-v0.13.3 using old permission-reply shape (claude-agent-acp ≥0.20 rejects it) | Upgrade Bridge to v0.13.3+; check `auto-allow permission` lines in Bridge log vs. tool_call status |
| Pipeline step (codex) fails with `agent timeout (idle 300s)` | PTY codex produced no stdout for 300s during long generation | Break the prompt into smaller steps, move codex earlier in the sequence, or swap to an ACP agent (kiro/claude) for that step |
| `POST /harness` returns 200 but the first call to the new agent fails | Profile preset unknown, or harness-factory binary not reachable from Bridge | Check `/harness/presets` for valid preset names; verify `harness.binary` in `config.yaml` is in Bridge's PATH; inspect Bridge log for `spawning: agent=<name>` errors |

## Notes

- Response time: typically 3–10s, longer for complex tasks
- Special characters in prompts are auto JSON-escaped by the script
- Multiple parts are concatenated into a single prompt
