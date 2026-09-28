[← Process Pool](process-pool.md) | [Testing →](testing.md)

> **Docs:** [Getting Started](getting-started.md) · [Tutorial](tutorial.md) · [Configuration](configuration.md) · [Agents](agents.md) · [API Reference](api-reference.md) · [Pipelines](pipelines.md) · [Async Jobs](async-jobs.md) · [Webhooks](webhooks.md) · [Client Usage](client-usage.md) · [Tools Proxy](tools-proxy.md) · [Security](security.md) · [Process Pool](process-pool.md) · [Lambda Burst](lambda-burst.md) · [Jev Router](jev-router.md) · [Testing](testing.md) · [Troubleshooting](troubleshooting.md)

# Lambda Burst (v0.45.0)

Run harness-factory agents as AWS Lambda invocations instead of local subprocesses, so a burst of work can fan out to 100+ concurrent agent instances that the host could never hold.

Disabled by default. When `lambda_pool.enabled` is false the Bridge behaves exactly as before: `/lambda-pool/*` returns 503 and any agent declaring `pool: "lambda"` is skipped with a warning.

> ⚠️ **Status: built, not yet recommended for production use.**
>
> The Bridge-side code is complete and unit-tested (`test_lambda_pool.py`,
> `test_lambda_integration.py`, `test_lambda_wrapper.py`, `test_lambda_e2e.py`),
> and the v0.45.0 audit fixed five defects before release. **But the feature has
> never been exercised end-to-end against a real Lambda** — every test is
> mock/moto-backed. It ships disabled by default, and the reference
> `config.yaml` carries no `lambda_pool` block and no `pool: "lambda"` agent.
>
> Before enabling it you must satisfy real prerequisites that a default VPC does
> **not** meet: Lambda needs **private** subnets with NAT egress (a
> public-subnet-only VPC is rejected by CDK — Lambda in a public subnet cannot
> reach the internet), CDK must be bootstrapped in the target region, and the
> deploying identity needs CloudFormation/Lambda/IAM/EC2/SecretsManager
> permissions. See [Deploy](#deploy) for the full list.
>
> Treat this as an experimental backend: fine to deploy and validate in a
> suitable VPC, but do not rely on it for production fan-out until you have run
> at least one real invocation through it. Known limits below still apply
> (lambda steps do not join a pipeline's shared workspace; no wait-for-slot —
> over-capacity fails fast).

## When to use it

The local [process pool](process-pool.md) is stateful and small — sessions are reused across turns, and `pool.max_processes` caps total subprocesses at single digits on a typical host. That is the right model for conversations and for pipelines that build on a shared workspace.

Lambda Burst is the opposite trade: stateless, no session reuse, but horizontally elastic. Each call is an independent invocation. Use it for fan-out shapes — score 200 documents, lint 50 repos, run one prompt against many inputs — and keep local agents for anything multi-turn.

| | Local pool | Lambda pool |
|---|---|---|
| Execution | subprocess on the Bridge host | Lambda invocation |
| State | session reused across turns | stateless, no reuse |
| Concurrency | `pool.max_processes` (single digits) | `max_concurrent` (100+) |
| Cold start | process spawn (~1s) | Lambda cold start (~2-5s), warmable |
| Workspace | shared `cwd`, files persist | `/tmp` inside the invocation, discarded |
| Cost | host capacity you already pay for | per-invocation |

## Architecture

```
 VPC
 ┌──────────────────────────────────────────────────────────┐
 │  EC2                          Lambda × N (N ≤ 100)       │
 │  ┌──────────────┐  invoke   ┌──────────────────────────┐ │
 │  │ ACP Bridge   │──────────▶│ handler.py               │ │
 │  │   :18010     │◀──────────│   ACP JSON-RPC over stdio│ │
 │  └──────────────┘  result   │ Layer: /opt/bin/         │ │
 │  ┌──────────────┐           │   harness-factory        │ │
 │  │ LiteLLM      │◀─ ─ ─ ─ ─ │                          │ │
 │  │   :4000      │   http    └──────────────────────────┘ │
 │  └──────────────┘                                        │
 │  LiteLLM stays private; API key lives in Secrets Manager │
 └──────────────────────────────────────────────────────────┘
```

The Lambda wrapper speaks the same ACP JSON-RPC dialect the Bridge speaks locally: `initialize` → `session/new` (with the profile) → `session/prompt`, collecting `agent_text_chunk` notifications and auto-replying to `fs/read_text_file`, `fs/write_text_file`, and `session/request_permission`.

## Deploy

Infrastructure lives in `infra/lambda-burst/` (CDK v2). Prerequisites: an existing VPC whose private subnets can reach your LiteLLM, Node.js with `aws-cdk`, and a `CGO_ENABLED=0` static build of harness-factory.

```bash
cd infra/lambda-burst/layer && ./build.sh          # stages bin/harness-factory
cd ../cdk && cp .env.example .env                  # fill VPC_ID, SUBNET_IDS, LITELLM_URL
npm install && npx cdk deploy
```

The stack creates the Lambda (Python 3.12, 512 MB, 5 min timeout, 1 GB ephemeral `/tmp`), the harness-factory layer, a Secrets Manager entry for the LiteLLM API key, and `reservedConcurrentExecutions` at `MAX_CONCURRENCY`. Populate the secret after deploy:

```bash
aws secretsmanager put-secret-value \
  --secret-id /acp-bridge/lambda-burst/litellm-api-key \
  --secret-string '{"apiKey":"sk-your-key"}'
```

Then wire the Bridge to it — the stack prints a ready-made `BridgeConfigSnippet` output.

## Configure

```yaml
lambda_pool:
  enabled: true
  function_name: "acp-bridge-harness-burst"     # CDK output FunctionName
  region: "us-east-1"
  max_concurrent: 100                           # Bridge-side brake
  timeout: 300                                  # per invocation, seconds
  default_model: "bedrock/anthropic.claude-sonnet-4-6"

agents:
  burst-worker:
    enabled: true
    pool: "lambda"                              # "local" (default) | "lambda"
    description: "Burst worker (harness-factory on Lambda)"
    model: "bedrock/anthropic.claude-sonnet-4-6"
    profile:
      tools:
        fs: { permissions: [read, list, write] }
      resources:
        timeout: 300s
```

A `pool: "lambda"` agent needs no `command` or `acp_args` — the binary is in the layer, not on the host. It appears in `/agents` tagged `lambda` and is callable through `/runs`, `/jobs`, and `/pipelines` like any other agent.

Keep `max_concurrent` at or below the Lambda reserved concurrency, and `timeout` at or below the Lambda function timeout. `max_concurrent` is the Bridge-side brake: exceeding it returns an at-capacity error rather than queuing (unlike the local pool's [bounded wait](process-pool.md), there is no wait-for-slot behavior here).

## Endpoints

| Endpoint | Purpose |
|----------|---------|
| `GET /lambda-pool/status` | function name, region, cap, in-flight count, cumulative invocations/errors |
| `POST /lambda-pool/invoke` | single invocation; 200 on `completed`, 502 otherwise |
| `POST /lambda-pool/invoke-batch` | fan out N prompts in parallel |
| `POST /lambda-pool/scale` | pre-warm containers with lightweight `__warmup__` pings |
| `POST /lambda-pool/drain` | wait for in-flight invocations to finish |

```bash
curl -s -X POST http://localhost:18010/lambda-pool/invoke-batch \
  -H "Authorization: Bearer $ACP_BRIDGE_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"prompts": [{"prompt": "score doc 1"}, {"prompt": "score doc 2"}]}'
```

`invoke-batch` always returns one result per input, positionally aligned. A per-item failure — including an at-capacity rejection — comes back as a `status: "error"` entry for that item; it never discards the results of items that succeeded.

Pre-warming is best-effort: `scale` fires async (`InvocationType: Event`) pings that the wrapper answers immediately without spawning an agent, and counts only HTTP 202 as warmed. It reduces cold starts ahead of a known burst but guarantees nothing about which containers survive.

## Security

| Concern | Handling |
|---------|----------|
| LiteLLM API key | Secrets Manager, read at runtime and cached per warm container — never in environment variables or the CloudFormation template |
| Bridge → Lambda payload | prompt, profile, model, timeout, session_id only; no credentials |
| Network | Lambda runs in private subnets; egress restricted to the LiteLLM port within the VPC plus HTTPS for Bedrock |
| LiteLLM exposure | stays on the private network, never published |
| Endpoint auth | `/lambda-pool/*` sits behind the standard Bearer token middleware; nothing is exempt |

The wrapper auto-approves `session/request_permission` with `proceed_always`, exactly as the local trust-all-tools setup does. Tool reach is bounded by the harness-factory profile you configure, so scope `tools` deliberately — the same discipline as any local harness agent.

## Notes

Each invocation gets a unique session id even when a caller supplies a stable one. Slots are keyed by invocation, not by session, so concurrent calls occupy distinct slots and `max_concurrent` applies as written.

Lambda steps in a pipeline do **not** participate in the shared workspace: the invocation's `/tmp` is discarded when it returns, so only the step's text output flows downstream. A step that must produce a file for a later step belongs on a local or mesh agent — declared `type: file` artifacts would fail the [sequence fail-fast check](pipelines.md). Per-step fallback is also skipped for lambda steps: a lambda agent is absent from the local process pool, so retrying one there could not work, and a lambda agent is never chosen to rescue a failed local step either.

Failures degrade to a `[error] Lambda agent failed: ...` message part rather than raising, so a burst partially completing is visible per item instead of collapsing the whole run.
