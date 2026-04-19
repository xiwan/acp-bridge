# Process Pool

- Each `(agent, session_id)` pair maps to an independent CLI ACP subprocess
- Same session reuses subprocess across turns, context is automatically retained
- Crashed subprocesses are rebuilt automatically (context lost, user is notified)
- Idle sessions are cleaned up after TTL expiry
- `session/request_permission` is auto-replied with `allow_always` (Claude compatibility)
- LRU eviction when pool is full:
  1. Same-agent idle connection → reuse process (reset session, skip respawn)
  2. Any idle connection → evict least-recently-used
  3. All busy → return `pool_exhausted` error
- Health check every 60s: ping idle connections, kill unresponsive ones
- Ghost cleanup: kill orphaned agent processes from previous Bridge runs on startup
