# Pipeline — Multi-Agent Collaboration

## API Call

```bash
curl -s -X POST "$ACP_BRIDGE_URL/pipelines" \
  -H "Authorization: Bearer $ACP_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "mode": "<sequence|parallel|race|random>",
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
| `random` | Pick one randomly; others skipped |
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
```

Response fields: `pipeline_id`, `mode`, `status`, `steps` (or `participants` /
`topic` / `initial_context` / `config` / `turns` / `stop_reason` / `transcript`
for conversation), `shared_cwd`, `duration`.
`transcript` is an array of `{turn, agent, content, duration}`.

## Reply Format

After submit: `🔗 Pipeline submitted (mode: sequence, agents: kiro → claude)`

After completion: show each step's result with agent name.

## Prompt Best Practices

Lessons from 20+ real pipeline runs. Apply when crafting `steps[].prompt`.

### 1. Always give absolute paths via `{{shared_cwd}}`

Agents don't know where files are unless you tell them. Downstream steps
(especially QA) will loop on `fs_search` trying to locate files.

```
❌  "用 fs_read 读取 sudoku.html"
✅  "用 fs_read 读取 {{shared_cwd}}/sudoku.html"
```

### 2. Verify artifacts on disk, not agent self-report

Agents often claim "saved successfully" but the file is 0 bytes or missing.

```
❌  "写完后确认文件已保存"
✅  "写完后执行 wc -c output.html 确认文件大于 N 字节"
```

### 3. Chain summaries, not raw output

`output_as` captures the agent's full reply — including model failover noise
(`[model xxx failed, switching to yyy]`) and tool logs. This wastes downstream
tokens. Two mitigations:

- When artifact is on disk, let the next agent **read the file** instead of
  receiving a text blob: `"读取 {{shared_cwd}}/PRD.md"` rather than `"PRD 内容：{{prd}}"`
- When text relay is needed, instruct the agent to end with a clean summary:
  `"完成后用 --- 分隔，写 200 字以内摘要"`

### 4. Match preset to task verb

| Task verb | Need | Wrong preset |
|-----------|------|--------------|
| write / save / create | `developer` `writer` `admin` | `reader` `reviewer` |
| read / review / analyze | any | — |
| run command | `executor` `operator` `developer` | `reader` `writer` |

Read-only preset + write task → agent loops or produces 0-byte file.

### 5. QA / review steps: specify tool + strong model

- Tell it which tool: `"使用 fs_read（不是 fs_search）读取文件"`
- Large files (>10KB) need large context — specify `"model": "claude-sonnet"`
  instead of `"auto"`
- Add read-only constraint if QA should not modify files

### 6. Static agents for writing, harness for reading

Static agents (kiro/claude) have reliable tool use and no sandbox restrictions.
Harness agents have sandboxed `fs` (limited to `shared_cwd`) and model
compatibility varies — some models emit tool calls in formats harness-factory
doesn't recognize, resulting in no actual execution.

| Task type | Best choice | Why |
|-----------|-------------|-----|
| Write files, run commands | static `kiro` / `claude` | Reliable tool use, no sandbox |
| Read/review/analyze within `shared_cwd` | harness (`reviewer` / `analyst`) | Sandboxed, read-only, cheap |
| Generate code, write tests | static `claude` | Needs fs_write + shell |

Typical pattern: static agent produces artifacts → harness agent reviews them.

### 7. Avoid PTY agents (Codex) in later sequence steps

PTY has no session memory and 300s idle timeout. Put Codex in step 1 or use
it in `parallel` / `race`. Later steps with accumulated context → timeout.

### 8. Keep prompt language consistent

One language per pipeline. Mixing causes agents to switch output language,
QA to misinterpret artifacts, and wastes tokens on implicit translation.

### 9. Recommended sequence template

```json
{
  "mode": "sequence",
  "steps": [
    {
      "agent": "<write-capable>",
      "prompt": "<task>。完成后执行 wc -c <file> 确认非空。",
      "output_as": "summary1"
    },
    {
      "agent": "<write-capable>",
      "prompt": "读取 {{shared_cwd}}/<prev-file>，<task>。完成后执行 wc -c <file> 确认非空。"
    },
    {
      "agent": "<review-capable>",
      "prompt": "用 fs_read 读取 {{shared_cwd}}/<file> 完整源码，逐项验收。直接输出报告，不要保存文件。"
    }
  ]
}
```

Key: write steps verify with `wc -c`; read steps use `{{shared_cwd}}/filename`;
chain via file on disk rather than `{{var}}`; QA step no `output_as`.
