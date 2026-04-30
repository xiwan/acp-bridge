# LLM Usage Tracking

Bridge records token usage and cache stats for all LLM calls routed through LiteLLM.

## How It Works

LiteLLM sends a `generic_api` callback to `POST /internal/llm-callback` after each successful call. Bridge stores the data in `data/usage.db` (SQLite, auto-created).

Agents that go through LiteLLM (codex, qwen, harness, opencode) are tracked automatically. Agents that call Bedrock directly (kiro, claude, hermes) are not.

## Endpoints

### `GET /usage?hours=24&model=`

Aggregated stats: total calls, tokens, cache rate, per-model breakdown.

### `GET /usage/recent?limit=20`

Recent call details: model, tokens, cached tokens, duration.

### `/litellm/{path}`

Transparent proxy to LiteLLM. Use for ad-hoc queries (e.g. `/litellm/v1/models`).

## Examples

```bash
# Aggregated stats (last 24h)
$ACP_CLIENT --raw GET /usage

# Last 10 calls
$ACP_CLIENT --raw GET "/usage/recent?limit=10"

# Filter by model
$ACP_CLIENT --raw GET "/usage?model=bedrock/deepseek.v3.2"

# List LiteLLM models
$ACP_CLIENT --raw GET /litellm/v1/models
```
