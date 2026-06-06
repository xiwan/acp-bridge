[← API Reference](api-reference.md) | [Security →](security.md)

> **Docs:** [Getting Started](getting-started.md) · [Tutorial](tutorial.md) · [Configuration](configuration.md) · [Agents](agents.md) · [API Reference](api-reference.md) · [Pipelines](pipelines.md) · [Async Jobs](async-jobs.md) · [Webhooks](webhooks.md) · [Client Usage](client-usage.md) · [Tools Proxy](tools-proxy.md) · [Security](security.md) · [Process Pool](process-pool.md) · [Testing](testing.md) · [Troubleshooting](troubleshooting.md)

# A2A Mesh

ACP Bridge can optionally join a decentralized A2A mesh. **L0** handles discovery: nodes publish their local agents as A2A skills, announce themselves to seed peers, and keep a peer table. **L1** adds remote invocation: a peer (or any A2A client) can call this node's local agents via `POST /a2a`. **L2** adds the client side + routing: a peer's agent that this node lacks is registered as a transparent local handler, so calling it via `/runs` (or `/jobs`, `/pipelines`) is automatically routed to the peer that owns it. **L3** lets a pipeline span nodes: a step whose agent lives on a peer relays its `shared_cwd` workspace via S3 (round-trip), so multi-step pipelines work across the mesh. Connecting to any mesh node thus lets you use every node's agents.

## Design Philosophy

<!-- The power of an analogy lies in its brevity — over-explaining dilutes it. -->

**You don't trust a miner's claim; you trust the verifiable hash chain.**

The same principle drives A2A Mesh design. We never assume a remote agent is honest, available, or deterministic. Instead, we build trust from *verifiable outcomes*: every cross-node invocation produces an auditable input→output chain — traceable, replayable, independently checkable. An agent's word is cheap; its receipts are not.

Discovery follows the gossip model: nodes announce capabilities, peers propagate what they observe, and the mesh converges on a shared view of the world without any central registry. No single node is authoritative — the topology is an emergent property of ongoing protocol exchanges, not a declared truth.[^1]

[^1]: Gossip guarantees *everyone hears* — it does not guarantee *what they hear is true*. Unlike blockchain consensus, gossip alone has no Sybil resistance or Byzantine fault tolerance. Truth verification is deliberately delegated to the application layer: hop limits, mesh-token auth, and artifact checksums serve as our "proof-of-work" equivalent.

This yields a trustless-by-default posture: orchestration never relies on trust in a single agent's reliability. Circuit breakers, hop limits, and artifact checksums exist so the system *verifies rather than believes*. If a result can't be validated, it's retried or failed — never silently accepted.

**The asymmetry constraint**: verifying must be *far cheaper* than re-executing. A Merkle proof works because it's O(log n) against O(n) recomputation. Our verification mechanisms follow the same economics — a checksum comparison or schema validation costs milliseconds against minutes of agent execution. If your verification approaches the cost of re-running the task, the black-box model collapses. Design verification to be lightweight, structural, and fast.

> *"A verifiable black box is a good architectural boundary. A runnable black box is just a monolith."*

## Enable Mesh

```yaml
mesh:
  enabled: true
  node_id: "node-a"
  self_url: "http://10.0.2.100:18010"
  announce_interval: 300
  max_hops: 1
  token: "${MESH_TOKEN}"
  seeds:
    - "http://10.0.3.50:18010"
  pricing:
    model: "free"
    rate: 0
```

When `mesh.enabled` is omitted or false, Bridge does not register mesh endpoints and does not start the announce loop.

### Enable via the installer

`install.sh` can configure the mesh for you (fresh install or update). After the S3 step it asks:

```
? Enable A2A Mesh? [y/N]:
```

If you answer yes, it prompts for the node id (defaults to the hostname), this node's `self_url`
(defaults to the host's private IP on port 18010), seed peer URLs (comma-separated, optional), and
a mesh token. Leave the token blank to auto-generate one, or paste an existing token to **join an
existing mesh** — every node in the same mesh must share the identical token.

The installer writes the token to `.env` as `MESH_TOKEN` (never into `config.yaml`, which only
references `${MESH_TOKEN}`), and prints just the token's last 4 characters so you can match it
across nodes without exposing the secret. On update, an existing `mesh:` section or `MESH_TOKEN`
is preserved, not overwritten.

### Peer Liveness

Nodes stay connected through periodic **announce**, not a fast ping/pong heartbeat:

- Every `announce_interval` seconds (default **300s / 5 min**), each node `POST`s its Agent Card
  to all seeds + known peers (`announce_loop`). The target replies with its own Card and peer
  list, so a single announce refreshes `last_seen` on **both** sides — which is why seeding one
  direction is enough for both peer tables to populate.
- After each cycle, `mark_stale` flags any peer whose `last_seen` is older than
  `announce_interval * 3` (default **900s / 15 min**) as `healthy: false`.
- L2 routing only registers agents from `healthy` peers. When a peer goes stale, its remote
  agents are dropped from the local route set, so calls stop forwarding to a dead node.
- Discovery is gossip-based: announce also exchanges each node's peer list, so a node learns
  "peers of peers" (added as `unhealthy` placeholders until first direct contact succeeds).

**Tuning:** with defaults, a node that dies takes up to ~15 min to be marked unhealthy. For
faster failure detection, lower `mesh.announce_interval` (e.g. `60` → stale after 180s) at the
cost of more frequent announce traffic.



### `GET /.well-known/agent.json`

Public Agent Card endpoint. It exposes capability metadata only.

```bash
curl -s http://localhost:18010/.well-known/agent.json
```

Each local enabled agent appears as a skill. If capability metadata is present in config, tags and version are included. Each skill also declares `pricing` with `model: "free"` in L0.

### `POST /a2a/announce`

Peer-to-peer discovery endpoint. This uses `mesh.token`, not the global `ACP_BRIDGE_TOKEN`.

```bash
curl -s -X POST http://localhost:18010/a2a/announce \
  -H "Authorization: Bearer $MESH_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"agent_card":{"url":"http://peer:18010","skills":[]},"peers":[]}'
```

The response includes this node's Agent Card and known peers.

### `GET /a2a/peers`

Debug view of the peer table. This endpoint remains protected by the global Bridge token.

```bash
curl -s http://localhost:18010/a2a/peers \
  -H "Authorization: Bearer $ACP_BRIDGE_TOKEN"
```

## Security Model

`/.well-known/agent.json` is public by design. `/a2a/announce` and `/a2a` both bypass the global Bridge token and use the separate `mesh.token` secret instead, so peer nodes authenticate on the mesh plane. `/a2a/peers` stays behind global auth because it exposes internal topology.

## `POST /a2a` (L1 — remote invocation)

JSON-RPC 2.0 entry point that lets a peer invoke this node's local agents. Authenticated with `mesh.token` (not the global Bridge token). Registered only when `mesh.enabled=true`.

### `tasks/send`

Synchronously runs a local agent (named by `params.skill`) through the same handler path as `/runs`, including fallback and circuit-breaker behaviour. The A2A message text becomes the prompt; remote callers get a fresh per-agent session on this node.

```bash
curl -s -X POST http://localhost:18010/a2a \
  -H "Authorization: Bearer $MESH_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tasks/send",
       "params":{"skill":"kiro",
                 "message":{"parts":[{"type":"text","text":"Say hello"}]}}}'
```

Response (text result + reserved billing metadata, free in L1):

```json
{"jsonrpc":"2.0","id":1,
 "result":{"status":{"state":"completed"},
           "artifacts":[{"parts":[{"type":"text","text":"..."}]}],
           "metadata":{"usage":null,"cost":{"amount":0,"currency":"USD"}}}}
```

### `tasks/get`

Query a previously submitted task by id (maps to the local job store).

```bash
curl -s -X POST http://localhost:18010/a2a \
  -H "Authorization: Bearer $MESH_TOKEN" \
  -d '{"jsonrpc":"2.0","id":2,"method":"tasks/get","params":{"id":"<job_id>"}}'
```

Errors use JSON-RPC codes: `-32601` unknown skill/method, `-32000` agent error, `-32001` task not found, `-32700` parse error.

**Not in L1**: `tasks/sendSubscribe` (SSE streaming) and `tasks/cancel` are deferred.

## Mesh Routing (L2 — calling out to peers)

When `mesh.enabled=true`, each announce cycle reconciles the peer table: for every skill a healthy peer has that this node does **not** serve locally, a transparent `a2a-remote` handler is registered into the local agent set. Calling that agent via `/runs`, `/jobs`, or `/pipelines` is then automatically forwarded to the owning peer over L1 `tasks/send`. No client change is needed — routing is invisible.

Rules:
- **Local priority** — a skill served locally is never shadowed by a remote one.
- **1-hop limit** — outbound forwards carry `X-A2A-Hop: 1`; a node receiving a hopped request refuses to forward it again (JSON-RPC error `-32011`), preventing cascades.
- **Single-turn** — L2 does not guarantee multi-turn session continuity across nodes; a remote call gets a fresh session on the owning node.
- **Free** — cross-node calls remain unbilled (pricing placeholders only).

```yaml
# node-a (no claude locally) seeds node-b (has claude)
mesh:
  enabled: true
  node_id: "node-a"
  self_url: "http://10.0.2.100:18010"
  token: "${MESH_TOKEN}"
  seeds: ["http://10.0.3.50:18010"]
```

```bash
# After discovery, claude appears in node-a's /agents and routes to node-b:
curl -s -X POST http://10.0.2.100:18010/runs \
  -H "Authorization: Bearer $ACP_BRIDGE_TOKEN" -H "Content-Type: application/json" \
  -d '{"agent_name":"claude","input":[{"parts":[{"content":"hi","content_type":"text/plain"}]}]}'
```

**Not in L2**: cross-Bridge pipelines + S3 artifact passing (L3), multi-hop routing, distributed session consistency, remote load-balancing.

### Identifying local vs remote agents

`GET /agents` marks where each agent runs, so a client can tell a local agent from one routed through the mesh:

- **`description`** — a remote agent shows the peer's real description plus a readable location suffix, e.g. `Echo reference agent (via mesh@node-b)`. Local agents keep their own description with no suffix.
- **`metadata.tags`** — structured, queryable markers:
  - local agents are tagged `local` (followed by their own tags)
  - remote agents are tagged `mesh`, `node:<peer-node-name>`, `peer:<peer-url>` (followed by the peer's own skill tags)

The peer node name comes from the peer's Agent Card `name` field (`acp-bridge@<node>`); description/tags are carried in the card's `skills` and surfaced at reconcile time. A client can filter `tags` for `local` vs `mesh` and read the exact owning node from `node:<name>`.

Remote routing is transparent to **async jobs** too: a `POST /jobs` targeting a peer-only agent runs the agent on the owning node (via the SDK agent registry) and stores the result locally, just like a local job.

## Cross-Bridge Pipelines (L3 — workspace relay)

A pipeline's `shared_cwd` is single-machine. When a step's agent lives on a peer (an L2 remote skill), L3 relays the workspace so the step can run there and the result comes back.

> **S3 is a hard prerequisite.** All mesh nodes must share one S3 bucket (set `s3.bucket` in `config.yaml`). The originating node needs S3 write access; peers need none (they use presigned URLs). If S3 is unavailable, a cross-node pipeline step **fails with a clear error** rather than silently losing files. Local-only pipelines never touch S3 and are unaffected.

Flow (A originates, step runs on B):
1. A tars the authoritative `shared_cwd`, uploads it, and generates a presigned GET (download) + presigned PUT (upload).
2. A calls B's `POST /a2a` (`tasks/send`) with `workspace_in_url` / `workspace_out_url` (and `X-A2A-Hop: 1`).
3. B downloads + unpacks the workspace to a temp dir, runs the agent with that dir as cwd, re-packs, and uploads to the PUT URL.
4. A downloads the result and merges it back into `shared_cwd` — so `/pipelines/{id}/artifacts` still reflects the truth on A.

Only the cross-node boundary triggers S3; consecutive local steps keep using `shared_cwd` directly. Multi-step cross-node chains work in `sequence` mode (each step relays the latest workspace), and `parallel` mode isolates each step in its own `shared_cwd/<agent>/` subdir so concurrent cross-node steps don't clobber each other.

## Two-Node Walkthrough (verify L0–L3)

A complete end-to-end setup with two Bridges — **node-a** (`172.31.15.10`) and **node-b**
(`172.31.6.197`) — plus a shared S3 bucket. Replace IPs with your own.

### 1. Configure both nodes

On each node add a `mesh:` block (or answer the installer's `Enable A2A Mesh?` prompt). Both
nodes must share the **same** `MESH_TOKEN` in `.env`.

```yaml
# node-a config.yaml
mesh:
  enabled: true
  node_id: "node-a"
  self_url: "http://172.31.15.10:18010"
  token: "${MESH_TOKEN}"
  seeds: ["http://172.31.6.197:18010"]   # point at node-b
  pricing: { model: "free", rate: 0 }
```

```yaml
# node-b config.yaml — self_url is itself, seeds point back at node-a
mesh:
  enabled: true
  node_id: "node-b"
  self_url: "http://172.31.6.197:18010"
  token: "${MESH_TOKEN}"
  seeds: ["http://172.31.15.10:18010"]
  pricing: { model: "free", rate: 0 }
```

Seeding one direction is enough (announce is bidirectional), but seeding both converges faster.
Copy the same token to both `.env` files — match the last 4 characters to confirm.

Restart both: `./bridge-ctl.sh restart`.

### 2. L0 — discovery

```bash
source .env
curl -s http://127.0.0.1:18010/.well-known/agent.json | jq .name   # not 404 = mesh up
curl -s http://127.0.0.1:18010/a2a/peers -H "Authorization: Bearer $ACP_BRIDGE_TOKEN" | jq
```

The peer table should list the other node with `"healthy": true` and its `skills`.

### 3. L1 — explicit remote invocation

Call a peer's agent directly over `POST /a2a` (uses `MESH_TOKEN`):

```bash
curl -s -X POST http://172.31.6.197:18010/a2a \
  -H "Authorization: Bearer $MESH_TOKEN" -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tasks/send",
       "params":{"skill":"harness","message":{"parts":[{"type":"text","text":"say hi"}]}}}'
```

A `result.artifacts[].text` reply confirms the cross-node call path works.

### 4. L2 — transparent routing

To see `/runs` route to a peer, the agent must be **peer-only** (local priority means a local
agent of the same name is never shadowed). On node-a, temporarily disable the local copy:

```yaml
# node-a config.yaml
  harness:
    enabled: false   # so node-b's harness is the one that routes
```

Restart node-a, wait one announce cycle, then call via the normal API — no peer address needed:

```bash
curl -s -X POST http://127.0.0.1:18010/runs \
  -H "Authorization: Bearer $ACP_BRIDGE_TOKEN" -H "Content-Type: application/json" \
  -d '{"agent_name":"harness","input":[{"parts":[{"content":"Run: echo I-am-on-the-peer","content_type":"text/plain"}]}]}'
```

The output proves it executed on node-b (e.g. its profile's shell allowlist applies).

### 5. L3 — cross-node pipeline

With a peer-only agent and a shared S3 bucket, a pipeline step runs on the peer and its workspace
is relayed back:

```bash
curl -s -X POST http://127.0.0.1:18010/pipelines \
  -H "Authorization: Bearer $ACP_BRIDGE_TOKEN" -H "Content-Type: application/json" \
  -d '{"mode":"sequence","steps":[
        {"agent":"harness","prompt":"Write a file proof.txt with content CROSS_NODE_L3_OK in the cwd, then reply done.","timeout":120}]}'
# poll GET /pipelines/{id}; then confirm the file came back:
curl -s http://127.0.0.1:18010/pipelines/<id>/artifacts -H "Authorization: Bearer $ACP_BRIDGE_TOKEN"
```

`artifacts` listing `proof.txt` means node-b's write was tar'd → S3 → merged back into node-a's
authoritative `shared_cwd`.

### Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| `/.well-known/agent.json` returns 404 | mesh not enabled on that node | set `mesh.enabled: true` and restart |
| `/a2a/announce` or `/a2a` returns `unauthorized` | `MESH_TOKEN` differs between nodes | use the identical token everywhere (match last 4 chars) |
| peer table stays `[]` | peer not restarted, or first announce cycle hasn't run yet | restart the peer; wait up to `announce_interval` (default 300s) |
| `/runs` doesn't route to the peer | a local agent of the same name takes priority | disable the local agent, or use a peer-only agent name |
| cross-node pipeline step fails immediately | S3 unavailable / no write access on the originating node | configure `s3.bucket` and ensure the origin node can write to it |
| `/a2a/peers` returns `unauthorized` from another node | that endpoint uses the **global** Bridge token, which is per-node | query each node's peer table with that node's own `ACP_BRIDGE_TOKEN` |
