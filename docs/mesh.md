[← API Reference](api-reference.md) | [Security →](security.md)

> **Docs:** [Getting Started](getting-started.md) · [Tutorial](tutorial.md) · [Configuration](configuration.md) · [Agents](agents.md) · [API Reference](api-reference.md) · [Pipelines](pipelines.md) · [Async Jobs](async-jobs.md) · [Webhooks](webhooks.md) · [Client Usage](client-usage.md) · [Tools Proxy](tools-proxy.md) · [Security](security.md) · [Process Pool](process-pool.md) · [Testing](testing.md) · [Troubleshooting](troubleshooting.md)

# A2A Mesh

ACP Bridge can optionally join a decentralized A2A mesh. **L0** handles discovery: nodes publish their local agents as A2A skills, announce themselves to seed peers, and keep a peer table. **L1** adds remote invocation: a peer (or any A2A client) can call this node's local agents via `POST /a2a`. Mesh routing — calling *out* to a peer that owns an agent you lack — is L2 and not yet implemented.

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

## Endpoints

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
