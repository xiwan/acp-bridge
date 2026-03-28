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

## Notes

- Response time: typically 3–10s, longer for complex tasks
- Special characters in prompts are auto JSON-escaped by the script
- Multiple parts are concatenated into a single prompt
