[← Lambda Burst](lambda-burst.md) | [Testing →](testing.md)

> **Docs:** [Getting Started](getting-started.md) · [Tutorial](tutorial.md) · [Configuration](configuration.md) · [Agents](agents.md) · [API Reference](api-reference.md) · [Pipelines](pipelines.md) · [Async Jobs](async-jobs.md) · [Webhooks](webhooks.md) · [Client Usage](client-usage.md) · [Tools Proxy](tools-proxy.md) · [Security](security.md) · [Process Pool](process-pool.md) · [Lambda Burst](lambda-burst.md) · [Jev Router](jev-router.md) · [Testing](testing.md) · [Troubleshooting](troubleshooting.md)

# Jev Router (v0.47.0; tools in criteria since v0.47.1; tuned example metadata since v0.47.2)

Let [TypeSafe AI's Jev](https://docs.typesafe.ai) decide which agent runs a task.

Jev is a *System One* model: it does not generate text. You send it a `state` and typed
questions; it returns typed answers with a calibrated probability distribution and a
`confidence` score. The Bridge uses exactly one **Choice** question — "which of these
agents should execute this task?" — with one option per eligible local agent plus
`other`, and hands the request to the winner unchanged.

Disabled by default. When `router.enabled` is false nothing is registered and every
existing call path behaves exactly as before.

## How it works

```
POST /runs  {agent_name: "auto", ...}          POST /jobs {agent_name: "auto", ...}
              │                                            │
              ▼                                            ▼
      virtual agent "auto"  ──►  JevRouter.decide(prompt)  ──►  Jev  POST /v1/systemone
              │                        │
              │        choice + confidence + probabilities
              │                        │
              │   confidence ≥ threshold && choice ∈ candidates ──► that agent
              │   otherwise (low confidence / other / any Jev error) ──► default_agent
              ▼
      yields a `route_info` part, then streams the chosen agent's output verbatim
```

- **Candidates** are built at startup from `config.yaml`: every agent that is
  `enabled`, local (`pool != lambda`), not the router itself, and not in
  `router.exclude`. If `router.candidates` is non-empty it is an allowlist.
  Agents with `trust: unrestricted` are never candidates unless explicitly listed.
- **Option descriptions** come from each agent's `description` (`what`),
  `capabilities.domains/tags` + `metadata.domains/tags`, and `capabilities.tools`
  (v0.47.1). Agents that only set `description` give Jev one sentence to work with;
  adding domains/tags/tools is the cheapest way to sharpen routing — no code change.
  List *distinctive* tools (`terraform`, `kubectl`, `playwright`); generic ones
  (`bash`, `read_file`) appear on every coding agent and only add noise. Measured
  2026-09-28: with kiro declaring terraform/kubectl/helm, "Terraform S3 bucket on AWS"
  moved from aws-devops 0.63 / kiro 0.35 to kiro 0.80 / aws-devops 0.20 — the agent
  that describes itself best wins, so give `aws-devops` its own domains/tools if you
  want it to take those tasks.
- **Only a prefix of the prompt** (`max_state_chars`, default 6000) is sent to Jev.
- **Fail-safe**: Jev timeout, 4xx/5xx, malformed response, low confidence or an
  `other` answer all route to `default_agent`. A Jev outage can never fail a task.
- **Two dispatch paths, one decision**. `/runs` hits the virtual agent's SDK handler,
  which asks Jev and then streams the chosen agent's handler. `/jobs` has no request
  context to hand to a local agent, so `JobManager` resolves the route *before*
  dispatch: `job.agent` is rewritten to the concrete agent (that is what `GET /jobs/{id}`
  reports, and what fallback/cost bookkeeping sees) and the result is prefixed with the
  same `route_info` line.
- **Observability**: the first part of every response is a `route_info` message
  (same pattern as `fallback_info`) with a human line plus a JSON comment:

  ```
  🧭 Route: auto → opengame (jev: opengame, confidence 0.96, jev)
  <!-- {"agent":"opengame","reason":"jev","fallback":false,"jev_choice":"opengame",
        "confidence":0.96,"probabilities":{"opengame":0.96,"qwen":0.04,"trae":0.0},
        "model":"jev-1.13.0","latency_ms":64,"input_tokens":1057,"error":"","router":"auto"} -->
  ```

  `reason` is one of `jev`, `low_confidence`, `other`, `invalid_choice`,
  `jev_error:timeout|connection|rate_limit|http_<code>|bad_response`.

## Configuration

```yaml
router:
  enabled: true
  agent_name: "auto"                 # virtual agent name; must not collide with a real agent
  api_key: "${TYPESAFE_API_KEY}"     # .env only — never paste the key into config
  model: "jev-latest"                # pin e.g. "jev-1.13.0" once thresholds are tuned
  timeout: 10                        # seconds per Jev call
  confidence_threshold: 0.5          # below this -> default_agent
  default_agent: "kiro"              # must be a configured agent
  candidates: []                     # allowlist; empty = all eligible local agents
  exclude: ["trae"]                  # denylist applied after candidates
  max_state_chars: 6000              # prompt prefix sent to Jev
```

Put the key in `.env` under the name `TYPESAFE_API_KEY` (issue one at
<https://console.typesafe.ai/keys>); systemd loads `.env` via `EnvironmentFile`.

## Endpoints

Both require the normal `Authorization: Bearer` token. When the router is disabled
they return `503 {"enabled": false}` so clients can feature-detect.

### `POST /route/preview` — dry run

Ask Jev without executing anything.

```bash
curl -s -X POST http://127.0.0.1:18010/route/preview \
  -H "Authorization: Bearer $ACP_BRIDGE_TOKEN" -H 'Content-Type: application/json' \
  -d '{"prompt": "Make a small HTML5 endless-runner game with a jumping dinosaur"}'
```

```json
{
  "agent": "opengame", "reason": "jev", "fallback": false, "jev_choice": "opengame",
  "confidence": 0.96, "probabilities": {"opengame": 0.96, "qwen": 0.04, "trae": 0.0},
  "model": "jev-1.13.0", "latency_ms": 64, "input_tokens": 1057, "error": "", "router": "auto"
}
```

### `GET /route/status`

Router config (no secrets) plus counters: `decisions`, `routed`, `fallback`,
`jev_errors`, `by_agent`, `by_reason`, `input_tokens`.

### Using the virtual agent

```bash
# sync
curl -s -X POST http://127.0.0.1:18010/runs -H "Authorization: Bearer $ACP_BRIDGE_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"agent_name":"auto","input":[{"parts":[{"content":"Write a Terraform module for an S3 bucket","content_type":"text/plain"}]}]}'

# async job — nothing else changes; the job result starts with the route_info line
curl -s -X POST http://127.0.0.1:18010/jobs -H "Authorization: Bearer $ACP_BRIDGE_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"agent_name":"auto","prompt":"Refactor src/pipeline.py and run the tests"}'
```

## Security notes

- The key is read from `.env` (`chmod 600`, gitignored, loaded by systemd
  `EnvironmentFile`). It exists only inside the outbound HTTP client's headers; it is
  never logged, never returned by any endpoint, and `JevRouter.__repr__` omits it.
- The outbound client is built with `trust_env=False` (proxy env vars cannot
  redirect the call) and refuses a non-`https` `base_url`.
- Routing cannot escalate privilege: the caller already holds the Bearer token that
  lets them name any agent directly, and Jev can only pick from the configured
  candidate set. `trust: unrestricted` agents are opt-in via `candidates`.
- What leaves the host is the prompt prefix plus the agent descriptions from your
  config. TypeSafe states Jev is not trained on customer requests; see their
  [legal page](https://docs.typesafe.ai/legal) for DPA / zero-data-retention terms.
- Jev is text-only and strongest in English. CJK prompts tend to land in
  `low_confidence` and therefore on `default_agent` — check `/route/preview` with
  your real traffic before lowering the threshold.
- Phrasing matters: "text only, do not create files or use tools" turns a QA task
  into plain Q&A in Jev's eyes and it answers `other` (observed 0.33 for `qa-agent`
  vs 1.00 for the same task without that suffix). Describe the *job*, not the
  output format, and let the agent's description do the matching.

## Tuning the criteria

Jev sees nothing but the option descriptions and the task text, so routing quality is a
configuration problem: **the agent that describes itself best wins.** Three rules, measured
against `jev-1.13.0` on 2026-09-28 with the 10 local candidates:

1. **Give every agent one positioning sentence nobody else could claim.** "Kiro CLI agent"
   vs "AWS DevOps Agent" told Jev nothing about *build IaC and deploy* vs *investigate an
   incident on existing resources*; with those sentences the two never compete.
2. **Split overlapping generalists by job, not by vendor.** Six agents all saying
   `coding / terminal / open-source` produced 0.3–0.5 ties. After giving them distinct
   domains — claude = large multi-file refactors, codex = PR review / CI, qwen = quick
   cheap bug fixes / unit tests / boilerplate, opencode = repo chores (dependency
   upgrades, build/lint fixes, git), hermes = assistant with memory / messaging,
   light-agent = quick Q&A and small scripts — each task landed on its agent at ≥ 0.99.
3. **List distinctive tools only.** `terraform`, `playwright`, `cloudwatch-logs`,
   `cost-explorer` move decisions; `bash`, `read_file`, `write_file` appear on every
   coding agent and only add noise.

Effect on a 13-task probe with a clear owner (before → after): hits 12/13 → 13/13,
confidence 0.45–1.00 → all ≥ 0.99. Examples: "convert this JSON to CSV and total it"
qwen 0.45 (below threshold → default) → light-agent 1.00; "8am Telegram summary" hermes
0.55 → 1.00; "AWS bill up 40 %, find out why" aws-devops 0.68 → 1.00; "fix the score bug in
yesterday's game" qwen 0.42 → 1.00; "upgrade npm deps and fix the build" claude 0.49 →
opencode 1.00. Deliberately vague prompts still answer `other` (0.7) and fall back to
`default_agent`. Cost: the richer criteria raise input from ~1,090 to ~1,830 tokens per
decision (≈ $0.00008).

`config.yaml.example` ships this metadata for kiro, claude, codex, qwen, opencode, hermes
and aws-devops (v0.47.2); copy the pattern for any agent you add. Check your own traffic
with `POST /route/preview` after every metadata change — a restart is required for the
Bridge to rebuild the criteria.

## Cost and limits

Measured 2026-09-28 against `jev-1.13.0` with 11 candidates: 60–115 ms per decision,
~1,060 input tokens. TypeSafe bills input only ($0.042 / M tokens), so a decision
costs about $0.00004. Account limits are 1,200 requests/min and 250k tokens/s.

## Known limitations

- **Pipelines**: `agent: auto` inside a pipeline step is not supported yet
  (pipeline agent resolution lives in `src/pipeline.py`; a resolver hook is planned).
- **Mesh / lambda / dynamic harness agents** are never candidates; only agents
  declared in `config.yaml` and served locally are offered to Jev.
- One question per request; multi-factor routing (e.g. cost vs. quality) can be added
  later as extra Choice/Score questions in the same call at negligible latency.
