# Changelog

| Version | Date | Description |
|---------|------|-------------|
| v0.15.11 | 2026-04-19 | Webhook generic format: `format: "generic"` sends plain JSON POST to any HTTP endpoint (Hermes, custom). Messages auto-chunked at 1800 chars for Discord safety. Enables Hermes Agent as webhook callback target via its webhook adapter (`/webhooks/<route>` + `deliver` to any IM platform). README rewrite: landing page + docs/ split into 12 files. New ASCII banner with agent roster. Startup banner in main.py |
| v0.15.10 | 2026-04-19 | Project positioning: README one-liner and Features section reorganized into three layers (Gateway → Orchestration → IM Workflows). Hermes Agent integrated as ACP agent (8/8 compliance, 7/7 tests). Added to auto_detect, install.sh, config, AGENT.md, SKILL.md, and test suite |
| v0.15.9 | 2026-04-19 | Remove `random` pipeline mode (breaking). Per v0.15.8 `/stats/pipelines` data: 4/8 runs in 7 days with 50% success — lowest usage, lowest success. `/pipelines` now rejects `mode=random`. Dropped from pipeline.py dispatch, routes validator, SKILL.md planner table + decision tree, pipeline.md mode table, README summary table + diagram, test_pipeline.sh case 7. Other 4 modes (sequence/parallel/race/conversation) unchanged |
| v0.15.8 | 2026-04-19 | New `/stats/pipelines?hours=N` endpoint: per-mode aggregation (total / completed / failed / running / avg_duration / max_duration) sourced from existing SQLite `pipelines` table. Enables data-driven decisions about pipeline mode usage |
| v0.15.7 | 2026-04-19 | Observability: `AcpConnection.state` read-only property (`dead`/`busy`/`stale`/`idle`) computed from existing flags; exposed per session in `/health/agents`. Zero-risk additive change, no state-machine refactor |
| v0.15.6 | 2026-04-19 | Cross-service trace propagation: `acp-client.sh` forwards optional `ACP_TRACE_ID` env var as `X-Request-Id` header; OpenClaw/upstream callers can pass their own request id and see it stitched across Bridge logs (including internal httpx/httpcore sub-calls like LiteLLM health probe). SKILL.md Auth section documents the pattern |
| v0.15.5 | 2026-04-19 | Observability: (1) request trace_id — every HTTP request gets an `X-Request-Id` (accepts client-supplied, else generates 12-char hex); id propagates via contextvar through async logs (including third-party httpcore/uvicorn) and echoes in response header; (2) `/stats.tools_used` now aggregates by tool category (`Running`/`Reading`/`Editing`/...) instead of full command string, making the field actually useful |
| v0.15.4 | 2026-04-19 | Robustness: readline now tolerates `LimitOverrunError` (>64KB single line no longer kills connections); PTY agents gain `max_duration` hard cap (default 600s) to prevent runaway subprocesses whose output dribbles in below `idle_timeout`; README gains a `⚠️ Security Considerations` section covering the token-as-shell risk, `--trust-all-tools` implications, prompt injection, and recommended deployment shapes |
| v0.15.3 | 2026-04-18 | `install.sh` auto-installs systemd units before startup: generates `/etc/systemd/system/acp-bridge.service` (and `litellm.service` when Codex/Qwen is enabled), `daemon-reload` + `enable`, then starts via `systemctl start` instead of `start.sh` |
| v0.15.2 | 2026-04-18 | `install.sh` prints OpenClaw skill setup box after install: skill URL + `ACP_TOKEN` + `ACP_BRIDGE_URL` (detects LAN/WAN IP). README install section updated with matching guidance |
| v0.15.1 | 2026-04-18 | Session reuse per (agent, channel_id) to avoid spawning new CLI per Discord message; `POST /harness` supports `model` field to override auto model selection; Job formatters (Discord/Feishu/Fallback) now read from `default_formatter.yml` |
| v0.15.0 | 2026-04-18 | Pluggable message template system: all webhook/status messages (pipeline, job, agent) defined in `src/templates/default_formatter.yml`. Change message format by editing YAML only — no code changes needed. `PipelineFormatter` + `fmt()` helper replace hardcoded strings in pipeline.py and agents.py |
| v0.14.6 | 2026-04-18 | systemd lifecycle management + `bridge-ctl.sh` self-bootstrap script. Kiro agents inside Bridge can now safely restart Bridge via `./bridge-ctl.sh restart` (systemd-backed). OPERATIONS.md updated: Phase 0 gains bridge-ctl rules, Phase 6 uses systemd instead of Docker as primary restart method |
| v0.14.5 | 2026-04-18 | Enhanced `GET /health`: three-state status (ok/degraded/unhealthy), process pool watermark, system memory usage, per-agent summary, async job queue summary, LiteLLM dependency status, human-readable uptime |
| v0.14.4 | 2026-04-17 | SKILL planner: Step 2 gains a **capability–task gate** — presets with `Write? = no` must not be paired with persistence verbs (write/save/produce doc/create md/...); Step 3 makes this gate a hard stop before showing the plan card. Troubleshooting gains the recurring 0-byte-artifact symptom (incident `493987b9` PM/QA text-stranded PRD + report) |
| v0.14.3 | 2026-04-17 | SKILL planner: dependency-first mode selection. New mandatory `Task dependency` row in Step 3 decision summary (sequential / independent / shared); Step 5 cheatsheet prepended with a decision tree driven by dependency instead of user phrasing. Orchestration templates gain a `Dep.` column. Anti-pattern noted: don't pick `sequence` just because user said "先…再…" when tasks are actually independent |
| v0.14.2 | 2026-04-17 | Capture harness-factory 0.8.0's `activated.resolvedModel` on `session/new`; `GET /harness` response now includes `resolved_model` (the actual model picked, populated after first call). Backward compatible with older harness-factory and non-harness ACP agents |
| v0.14.1 | 2026-04-17 | SKILL.md Step 4 rules: (1) harness-creation echo must include `preset` + `model`; (2) pipeline/conversation results must append a duration breakdown (per-agent + total) with `stop_reason` for conversation mode |
| v0.14.0 | 2026-04-17 | SKILL.md structural rewrite for token efficiency: front-load Planning Workflow (hot path now in first ~90 lines vs. mid-doc previously); Preset capability matrix moved adjacent to Planning; compress Commands (17→1 line), Harness usage (17→5 lines), Output Rules (20→4 lines). 307 → 228 lines, no semantic changes |
| v0.13.7 | 2026-04-17 | Enrich SKILL.md preset table with `Write?` column (authoritative from `harness-factory -dry-run`) and `Recommended model` binding; warn `reviewer` must output via text reply (real incident: pipeline be8e1d8c hit `[loop detected: fs_read]` with no write target) |
| v0.13.6 | 2026-04-17 | New `skill/references/orchestration-patterns.md`: 10 preset templates for 2/3/4-5 agent orchestration (relay, dual-view, debate-2, race-2, review-write-test, fan-out-merge, roundtable-3, staged-pipeline, parallel-then-judge, dual-debate-then-judge). SKILL.md Step 5 points at it; >5 agents left to host LLM |
| v0.13.5 | 2026-04-17 | Skill doc hardening: dedupe pipeline content (single source = `pipeline.md`); add 3 real-bug troubleshooting rows (permission schema, PTY idle timeout, harness spawn vs. call); Planner Step 4.1 fallback guidance on failures; `CLAUDE_SKILL_DIR` host-injection note + fallback expansion |
| v0.13.4 | 2026-04-17 | Translate `skill/SKILL.md` Planner Workflow (Step 1–7), decision-summary card, duration estimation guide, preset-intent mapping, and frontmatter description to English. No behavior change; 2 intentional CJK confirmation aliases kept. |
| v0.13.3 | 2026-04-17 | Fix auto-allow permission response shape to match ACP 1.0 RequestPermissionOutcome schema (wrap in `outcome: {outcome: "selected", optionId}`); previously claude-agent-acp 0.20+ treated the old flat form as reject. Also: `start.sh` gains `--stop` / `--foreground` / `--restart` and default background mode with `/health` readiness wait |
| v0.13.2 | 2026-04-17 | Planner rules: enforce async for >60s tasks, add duration estimation guide; require full job_id/pipeline_id in response relay |
| v0.13.1 | 2026-04-17 | Planner enhancements (agent sourcing + decision-rich plan card) + skill dir reorg: scripts/ and references/ subdirs, removed stale archive |
| v0.13.0 | 2026-04-17 | Skill-side planner workflow: classify intent → fetch /agents → show plan table → `yes` to execute; adds clarification heuristics and common intent lookup |
| v0.12.3 | 2026-04-17 | Fix test_kiro.sh async-submit assertion to match client output (Submitted/已提交/job:) |
| v0.12.2 | 2026-04-16 | Smart install.sh: state detection (first run vs update), install missing agent CLIs, incremental config.yaml update, Node.js detection |
| v0.12.1 | 2026-04-16 | Interactive install.sh: agent selection, harness-factory fallback, token setup, config generation |
| v0.12.0 | 2026-04-16 | Zero-config auto-detect mode: no config.yaml needed, auto-discovers agent CLIs in PATH; one-line install.sh |
| v0.11.6 | 2026-04-14 | Fix dynamic harness cross-agent process reuse; fix card mode empty output (bash read merging tabs) |
| v0.11.5 | 2026-04-13 | Dynamic Harness API: runtime creation of harness-factory agents via HTTP (POST/GET/DELETE /harness) |
| v0.11.4 | 2026-04-13 | Fix health_check killing busy agent connections |
| v0.11.3 | 2026-04-12 | Prompt templates: reusable prompt definitions with variable rendering |
| v0.11.2 | 2026-04-12 | Agent call statistics API (GET /stats) |
| v0.11.1 | 2026-04-13 | Harness Factory integration: profile-driven lightweight agents |
| v0.11.0 | 2026-04-11 | Harness Factory support: profile-driven ACP agents via harness-factory binary |
| v0.10.13 | 2026-04-11 | Session close API (DELETE /sessions/{agent}/{session_id}) |
| v0.10.12 | 2026-04-09 | Pipeline conversation mode improvements |
| v0.10.11 | 2026-04-03 | OOM protection: memory-aware pool eviction，内存超 80% 自动驱逐空闲连接，降低默认池上限 |
| v0.10.10 | 2026-04-03 | Shared workspace for all pipeline modes: sequence/parallel/race/random 共享工作目录，agent 间文件可见 |
| v0.10.8 | 2026-04-02 | Conversation 修复增强：中文自动检测、每个 agent 首轮注入 topic、prompt 文件化管理、共享工作目录、transcript 空输出 fallback、webhook 完整内容分段推送 |
| v0.10.7 | 2026-04-02 | Pipeline conversation mode: 多 agent 多轮对话，Bridge 只传话 + SQLite 记录，支持 @mention 定向调度 |
| v0.10.6 | 2026-04-02 | Pipeline random mode: 从 steps 中随机选一个 agent 执行，其余跳过 |
| v0.10.5 | 2026-04-01 | Pipeline PTY support: Codex 等 PTY agent 可参与 pipeline (sequence/parallel/race)，.env auto-loader 统一环境变量管理 |
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
