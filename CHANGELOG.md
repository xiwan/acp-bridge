# Changelog

| Version | Date | Description |
|---------|------|-------------|
| v0.10.4 | 2026-04-01 | Connection pool LRU eviction: 满载时淘汰最不活跃的空闲连接，同 agent 优先复用进程，消灭 pool_exhausted |
| v0.10.3 | 2026-04-01 | Pipeline per-step webhook push: sequence逐步推送, parallel完成即推, race赢家即推, 最终推汇总耗时 |
| v0.10.2 | 2026-03-31 | Pipeline SQLite persistence — survives restarts, queryable via API |
| v0.10.1 | 2026-03-31 | Pipeline session_id fix, skill pipeline recognition rules |
| v0.10.0 | 2026-03-31 | Multi-agent pipeline: sequence, parallel, race execution (`POST /pipelines`) |
| v0.9.6 | 2026-03-31 | Webhook retry limit (max 5) + response body logging on failure |
| v0.9.5 | 2026-03-29 | File upload API: `POST /files` with configurable `upload_dir`, skill `--upload`, Web UI attach button |
| v0.9.2 | 2026-03-29 | AGENT.md rewrite: agent-first flow with clear 🤖/👤 boundaries |
| v0.9.1 | 2026-03-29 | README refresh: fix ASCII art arrow direction, add Web UI + direct HTTP path, remove screenshot |
| v0.9.0 | 2026-03-28 | Adaptive Web UI: native CSS variables, dark mode, responsive layout, drop jQuery + Tailwind |
| v0.8.4 | 2026-03-28 | Qwen Code + OpenCode agent support, test_qwen.sh, test_opencode.sh |
| v0.8.3 | 2026-03-21 | Web UI opt-in (`--ui`), chat persistence (SQLite), message folding, settings panel |
| v0.8.1 | 2026-03-19 | Docker image 618→439MB, agent-optimized README, test coverage 25→31 |
| v0.8.0 | 2026-03-19 | Docker light mode: gateway-only image with host agent mounting |
| v0.7.3 | 2026-03-18 | Request-level cwd, tools proxy fix, test improvements |
| v0.7.2 | 2026-03-18 | Multi-IM formatter (Discord/Feishu), unified target field, systemd |
| v0.7.1 | 2026-03-18 | ACP agent compliance test, AGENT_SPEC.md, echo-agent reference impl |
| v0.7.0 | 2026-03-17 | OpenClaw tools proxy, agent health probe, auto-recovery |
| v0.6.0 | 2026-03-15 | Codex PTY support, LiteLLM integration, acp-client.sh |
