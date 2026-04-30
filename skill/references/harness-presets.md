# Harness Preset Capability Matrix

Pick a preset from intent **and** check its `Write?` column before crafting the prompt.

| Intent | Preset | Write? | Recommended model |
|--------|--------|--------|-------------------|
| Read files, look at code | `reader` | no | `auto` |
| Run commands, inspect system | `executor` | no (shell only) | `claude-sonnet` |
| Fetch web pages, search | `scout` | no | `deepseek-v3` |
| Review code, inspect diffs | `reviewer` ⚠️ | **no** — output as text reply, not file | `claude-sonnet` / `deepseek-v3` |
| Analyze data, statistics | `analyst` | no (shell only) | `deepseek-v3` / `qwen3` |
| Research, gather info and summarize | `researcher` | no | `deepseek-v3` |
| Write code, run tests, commit | `developer` | **yes** | `claude-sonnet` |
| Write docs, look up references | `writer` | **yes** | `claude-sonnet` / `deepseek-v3` |
| Ops, deploy, network | `operator` | **yes** | `claude-sonnet` / `deepseek-v3` |
| Full permissions | `admin` | **yes** | `claude-sonnet` |

## Model compatibility

⚠️ `auto` may resolve to models whose tool-call format harness-factory doesn't recognize (e.g. minimax, kimi). For write steps or complex tool use, always specify `claude-sonnet` or `deepseek-v3`.

## Write safety rule

If `Write? = no`, do **not** instruct the agent to "save a report" — it will loop on `fs_read` until the harness cuts it off. For review + persisted report, pair a `reviewer` with a `writer` or `developer`, or just use static `claude`.

## Dynamic Harness (usage)

`POST /harness` with `{"profile": "<preset>", "system_prompt": "..."}` spawns an agent. Supports `"model": "<alias>"` or omit for `auto`. Full API + model list in [../AGENT.md](../AGENT.md).

```bash
curl -X POST "$ACP_BRIDGE_URL/harness" -H "Authorization: Bearer $ACP_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"profile":"operator","system_prompt":"Help the user build a weather-query skill"}'
$ACP_CLIENT -a <returned_name> "<prompt>"
curl -X DELETE "$ACP_BRIDGE_URL/harness/<name>" -H "Authorization: Bearer $ACP_TOKEN"   # cleanup
```
