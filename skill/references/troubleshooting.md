# ACP Bridge — Troubleshooting

Quick diagnosis table. For pipeline-specific prompt issues, see [pipeline.md](pipeline.md) § Best Practices.

| Symptom | Cause | Fix |
|---------|-------|-----|
| `❌ Connection failed` | Service not running or wrong address | Verify Bridge address and port |
| `unauthorized` | Incorrect token | Ask user to confirm token (do not echo it) |
| `forbidden` | IP not in allowlist | Contact Bridge admin to add IP |
| `422 Unprocessable` | session_id is not UUID format | Use UUID-format session_id |
| `pool_exhausted` | All connections busy | Wait 30s or reduce parallelism |
| `agent timeout (idle 300s)` on codex | PTY no stdout for 300s | Swap to ACP agent (kiro/claude), or split prompt |
| Step `completed` but output empty | Bridge permission-reply mismatch | Needs Bridge ≥ v0.13.3 |
| Pipeline step can't read shared_cwd file | Prior step didn't actually write | Rerun that step in `/cli` to verify |
| Harness `[loop detected: fs_read called N times]` | Read-only preset asked to write | Rewrite prompt to reply in text; or swap to `developer`/`writer`/static `claude` |
| Pipeline `completed` but 0-byte files | Preset `Write? = no` + persistence verb | Apply Step 2 capability gate; use static agent for write steps |
| Harness step completes instantly (<3s) with XML/markdown tool calls in output | Model emits tool-call format harness-factory doesn't recognize | Specify a compatible model (`deepseek-v3`, `claude-sonnet`) instead of `auto` |
| Discord push fails | Wrong `account_id` | Use `default` for Discord, `main` for Feishu |
| Discord 500 | Bad target format | DM: `user:<id>`, channel: `channel:<id>` |
| Long timeout on single call | CLI processing or session conflict | Ensure different agents use different session_ids |
