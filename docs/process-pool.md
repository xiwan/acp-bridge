[← Security](security.md) | [Testing →](testing.md)

> **Docs:** [Getting Started](getting-started.md) · [Tutorial](tutorial.md) · [Configuration](configuration.md) · [Agents](agents.md) · [API Reference](api-reference.md) · [Pipelines](pipelines.md) · [Async Jobs](async-jobs.md) · [Webhooks](webhooks.md) · [Client Usage](client-usage.md) · [Tools Proxy](tools-proxy.md) · [Security](security.md) · [Process Pool](process-pool.md) · [Testing](testing.md) · [Troubleshooting](troubleshooting.md)

# Process Pool

The process pool (`AcpProcessPool`) manages ACP agent subprocesses. Each `(agent, session_id)` pair maps to an independent CLI subprocess.

## Lifecycle

```
Request arrives → lookup (agent, session_id)
  ├─ Found idle connection → reuse (context retained)
  ├─ Not found, pool has capacity → spawn new subprocess
  │     initialize → session/new → session/prompt
  └─ Pool full → LRU eviction (see below) or pool_exhausted error
```

## Session Reuse

Same session reuses the same subprocess across turns — conversation context is automatically retained by the agent. This is the key advantage of ACP mode over PTY.

If a subprocess crashes mid-session, Bridge rebuilds it automatically. Context is lost, and the user is notified.

## LRU Eviction

When the pool is full and a new connection is needed:

1. **Same-agent idle connection** → reuse process (reset session, skip respawn) — fastest
2. **Any idle connection** → evict least-recently-used — reclaims a slot
3. **All connections busy** → return `pool_exhausted` error

## OOM Protection

When system memory exceeds `pool.memory_limit_percent` (default: 80%), Bridge proactively evicts idle connections to free memory before the OS OOM killer intervenes.

## Concurrency Safety

As of v0.18.0, `get_or_create()` is protected by an `asyncio.Lock`. This prevents a race condition where concurrent requests could all pass the capacity check before any of them inserted, causing the pool to exceed `max_processes`. With the lock, concurrent requests are serialized at the pool level — one acquires a slot, the next sees the updated count.

## Circuit Breaker

Each agent has an automatic circuit breaker (three-state: CLOSED → OPEN → HALF_OPEN → CLOSED):

- **CLOSED** (normal): calls pass through; failures are counted in a sliding window
- **OPEN** (tripped): after 5 consecutive failures or 50% failure rate in the window, all calls are rejected immediately for 30 seconds
- **HALF_OPEN** (probing): after the timeout, up to 3 test calls are allowed; success → CLOSED, failure → OPEN again

Rate-limit errors (`429`) are intentionally excluded from the failure count — they indicate client-side throttling, not agent fault.

Circuit breaker state is visible via the metrics system (see [Configuration](configuration.md) — Metrics section).

## Health Check

Every 60 seconds, Bridge pings all idle connections. Unresponsive subprocesses are killed and their slots freed. Connections stuck in busy state beyond `pool.busy_timeout` (default: 360s) are also killed.

## Automatic Fallback

When an agent call fails, Bridge automatically tries the next agent in the fallback chain (configurable via `fallback-chain.yaml` or `PUT /fallback-chain`). Up to 3 attempts are made. Fallback selection uses a scoring algorithm that considers:

- Circuit breaker state (OPEN agents are excluded)
- Whether the agent has idle connections (1.5× bonus)
- Success rate over the last hour
- Average response time
- Trend: declining success rate in the last 15 minutes incurs a penalty

## Ghost Cleanup

On startup, Bridge scans for orphaned agent processes from previous runs (e.g. after a crash) and kills them. This prevents resource leaks from stale subprocesses.

## Permission Auto-Reply

`session/request_permission` notifications from agents are auto-answered with `proceed_always`. This is required for Claude compatibility — without it, Claude hangs waiting for user approval.

## Configuration

```yaml
pool:
  max_processes: 8              # total subprocess limit
  max_per_agent: 4              # per-agent-type limit
  memory_limit_percent: 80      # OOM eviction threshold

server:
  session_ttl_hours: 24         # idle session cleanup
```

## See Also

- [Configuration](configuration.md) — pool settings
- [Agents](agents.md) — ACP vs PTY mode differences
- [Troubleshooting](troubleshooting.md) — `pool_exhausted` fixes
