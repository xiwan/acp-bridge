# Pipeline — Multi-Agent Collaboration

## API Call

```bash
curl -s -X POST "$ACP_BRIDGE_URL/pipelines" \
  -H "Authorization: Bearer $ACP_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "mode": "<sequence|parallel|race>",
    "steps": [
      {"agent": "<name>", "prompt": "<task>", "output_as": "<var>"},
      {"agent": "<name>", "prompt": "based on: {{<var>}}"}
    ]
  }'
```

## Modes

| Mode | Behavior |
|------|----------|
| `sequence` | Steps in order; `{{output_as}}` passes output to next |
| `parallel` | All concurrent; results merged |
| `race` | All concurrent; first to complete wins |
| `conversation` | Multi-turn dialog between agents |

## Conversation Mode

Supports both ACP agents (Kiro, Claude, Qwen, OpenCode) and PTY agents (Codex) — mix freely in any mode.

```bash
curl -s -X POST "$ACP_BRIDGE_URL/pipelines" \
  -H "Authorization: Bearer $ACP_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "mode": "conversation",
    "participants": ["kiro", "claude"],
    "topic": "Review the auth module",
    "config": {"max_turns": 8, "stop_conditions": ["DONE", "CONSENSUS"]}
  }'
```

- Bridge only relays "what the last agent said" — agents maintain their own context
- Agents can use `@agent_name` to direct messages; Bridge routes accordingly
- Stops on `STATUS: DONE`, `STATUS: CONSENSUS`, consecutive `PASS`, or max turns
- Full transcript stored in SQLite, returned via `GET /pipelines/<id>`

## Query

```bash
# Single pipeline (includes transcript for conversation mode)
curl -H "Authorization: Bearer $ACP_TOKEN" "$ACP_BRIDGE_URL/pipelines/<id>"

# List all
curl -H "Authorization: Bearer $ACP_TOKEN" "$ACP_BRIDGE_URL/pipelines"

# List artifacts in shared workspace
curl -H "Authorization: Bearer $ACP_TOKEN" "$ACP_BRIDGE_URL/pipelines/<id>/artifacts"
```

Response fields: `pipeline_id`, `mode`, `status`, `steps` (or `participants` /
`topic` / `initial_context` / `config` / `turns` / `stop_reason` / `transcript`
for conversation), `shared_cwd`, `paused`, `output`, `duration`.
`transcript` is an array of `{turn, agent, content, duration}`.

## Composable Pipelines (v0.19.0)

### Workspace Inheritance

Pass `shared_cwd` in `context` to reuse a previous pipeline's workspace:

```bash
# Phase 2 inherits Phase 1's workspace
curl -s -X POST "$ACP_BRIDGE_URL/pipelines" \
  -H "Authorization: Bearer $ACP_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "mode": "parallel",
    "context": {"shared_cwd": "<previous pipeline shared_cwd>"},
    "steps": [...]
  }'
```

### Output Extraction

Set `"output_schema": true` in conversation config — Bridge extracts JSON from the final agent turn into `output` field.

### Human-in-the-Loop

```bash
# Pause conversation
POST /pipelines/<id>/pause

# Resume
POST /pipelines/<id>/resume

# Inject message (auto-resumes if paused)
POST /pipelines/<id>/inject  {"message": "Use Phaser.js"}
```

Injected messages appear as `[Human]` turns in transcript.

## Reply Format

After submit: `🔗 Pipeline submitted (mode: sequence, agents: kiro → claude)`

After completion: show each step's result with agent name.

## Prompt Best Practices

### 1. Use absolute paths: `{{shared_cwd}}/filename` — agents won't find files otherwise

### 2. Verify with `wc -c` — agents claim success but file may be empty

### 3. Chain via file on disk, not `{{var}}` — avoids passing noisy tool logs downstream

### 4. Match preset to verb: write/create → `developer`/`operator`/`admin`; read/review → any

### 5. QA steps: specify `fs_read` explicitly + strong model for large files

### 6. Static agents (kiro/claude) for writing; harness for reading — harness has sandbox + model quirks

### 7. Avoid PTY (Codex) in later sequence steps — no session memory, 300s idle timeout

### 8. One language per pipeline — mixing wastes tokens on implicit translation

### 9. Sequence template

```json
{"mode": "sequence", "steps": [
  {"agent": "<writer>", "prompt": "<task>. Run wc -c <file> to confirm.", "output_as": "s1"},
  {"agent": "<writer>", "prompt": "Read {{shared_cwd}}/<file>, <task>. Run wc -c to confirm."},
  {"agent": "<reviewer>", "prompt": "fs_read {{shared_cwd}}/<file>, review. Output report only."}
]}
```

### 10. OpenGame constraints

- **`shared_cwd` must be `/tmp/opengame`** — internal sandbox blocks other paths, causing idle timeout
- **timeout 300-900s** — complex games take 3-5 min
- **harness deploy uses shell_exec** — `aws s3 cp` directly

```json
{"mode": "sequence", "steps": [
  {"agent": "opengame", "prompt": "<game desc>, game name: <name>", "timeout": 300},
  {"agent": "harness", "prompt": "Deploy /tmp/opengame/<name>.html to S3. Run: aws s3 cp /tmp/opengame/<name>.html s3://opengame-demo-summit-2026/<name>/index.html --content-type text/html --region us-east-1 && aws cloudfront create-invalidation --distribution-id E3MU4MKLH39XO9 --paths \"/<name>/*\". Report URL: https://d1x0y8igxbg2j0.cloudfront.net/<name>/", "timeout": 60}
], "context": {"shared_cwd": "/tmp/opengame"}}
