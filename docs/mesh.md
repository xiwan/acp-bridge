[← API Reference](api-reference.md) | [Security →](security.md)

> **Docs:** [Getting Started](getting-started.md) · [Tutorial](tutorial.md) · [Configuration](configuration.md) · [Agents](agents.md) · [API Reference](api-reference.md) · [Pipelines](pipelines.md) · [Async Jobs](async-jobs.md) · [Webhooks](webhooks.md) · [Client Usage](client-usage.md) · [Tools Proxy](tools-proxy.md) · [Security](security.md) · [Process Pool](process-pool.md) · [Testing](testing.md) · [Troubleshooting](troubleshooting.md)

# A2A Mesh

ACP Bridge can optionally join a decentralized A2A mesh. Mesh L0 only handles discovery: nodes publish their local agents as A2A skills, announce themselves to seed peers, and keep a peer table. Remote cross-node invocation is not implemented in L0.

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

`/.well-known/agent.json` is public by design. `/a2a/announce` bypasses the global Bridge token so peer nodes can use the separate mesh secret. `/a2a/peers` stays behind global auth because it exposes internal topology.
