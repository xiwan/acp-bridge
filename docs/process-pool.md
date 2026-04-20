[← Security](security.md) | [Testing →](testing.md)

> **Docs:** [Getting Started](getting-started.md) · [Tutorial](tutorial.md) · [Configuration](configuration.md) · [Agents](agents.md) · [API Reference](api-reference.md) · [Pipelines](pipelines.md) · [Async Jobs](async-jobs.md) · [Client Usage](client-usage.md) · [Tools Proxy](tools-proxy.md) · [Security](security.md) · [Process Pool](process-pool.md) · [Testing](testing.md) · [Troubleshooting](troubleshooting.md)

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

## Health Check

Every 60 seconds, Bridge pings all idle connections. Unresponsive subprocesses are killed and their slots freed.

## Ghost Cleanup

On startup, Bridge scans for orphaned agent processes from previous runs (e.g. after a crash) and kills them. This prevents resource leaks from stale subprocesses.

## Permission Auto-Reply

`session/request_permission` notifications from agents are auto-answered with `allow_always`. This is required for Claude compatibility — without it, Claude hangs waiting for user approval.

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
