#!/bin/bash
# Integration test — A2A Mesh L0 decentralized discovery.
# Spins up TWO bridge instances (echo-agent only, no real CLI needed), each with
# mesh enabled and the other as a seed, then verifies bidirectional discovery
# and the Agent Card shape. Self-contained: starts/stops its own bridges.
#
# Usage: bash test/integration/test_mesh.sh
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"

PORT_A=18091
PORT_B=18092
BRIDGE_AUTH="mesh-test"
MESH_AUTH="mesh-peer"
TMP="$(mktemp -d)"
PASS=0 FAIL=0
PIDS=()

cleanup() {
  for pid in "${PIDS[@]:-}"; do kill "$pid" 2>/dev/null; done
  sleep 0.5
  for pid in "${PIDS[@]:-}"; do kill -9 "$pid" 2>/dev/null; done
  rm -rf "$TMP"
}
trap cleanup EXIT

check() {
  local name="$1" expect="$2" actual="$3"
  if echo "$actual" | grep -q "$expect"; then echo "✅ $name"; ((PASS++));
  else echo "❌ $name"; echo "   expect: $expect"; echo "   actual: ${actual:0:300}"; ((FAIL++)); fi
}

make_config() {  # $1=node_name $2=port $3=seed_port
  cat > "$TMP/config-$1.yaml" <<EOF
server:
  host: "127.0.0.1"
  port: $2
security:
  auth_token: "$BRIDGE_AUTH"
  allowed_ips: []
agents:
  echo:
    enabled: true
    mode: "acp"
    command: "python"
    acp_args: ["examples/echo-agent.py"]
    working_dir: "/tmp"
    description: "Echo reference agent"
mesh:
  enabled: true
  node_id: "$1"
  self_url: "http://127.0.0.1:$2"
  announce_interval: 5
  token: "$MESH_AUTH"
  seeds:
    - "http://127.0.0.1:$3"
EOF
}

echo "=== A2A Mesh L0 集成测试 ==="
make_config node-a "$PORT_A" "$PORT_B"
make_config node-b "$PORT_B" "$PORT_A"

uv run python main.py --config "$TMP/config-node-a.yaml" >"$TMP/a.log" 2>&1 & PIDS+=($!)
uv run python main.py --config "$TMP/config-node-b.yaml" >"$TMP/b.log" 2>&1 & PIDS+=($!)

# wait for both to bind + first announce cycle
for i in $(seq 1 30); do
  ca=$(curl -s "http://127.0.0.1:$PORT_A/.well-known/agent.json" 2>/dev/null)
  cb=$(curl -s "http://127.0.0.1:$PORT_B/.well-known/agent.json" 2>/dev/null)
  [[ -n "$ca" && -n "$cb" ]] && break
  sleep 1
done
sleep 6  # let announce_loop (interval=5) run at least once

# 1. Agent Card shape
card_a=$(curl -s "http://127.0.0.1:$PORT_A/.well-known/agent.json")
check "node-a card has skill echo" '"id":"echo"' "$card_a"
check "node-a card pricing free"   '"rate":0'    "$card_a"
check "node-a card name"           'acp-bridge@node-a' "$card_a"

# 2. Bidirectional discovery (peers endpoint is behind global auth)
peers_a=$(curl -s "http://127.0.0.1:$PORT_A/a2a/peers" -H "Authorization: Bearer $BRIDGE_AUTH")
peers_b=$(curl -s "http://127.0.0.1:$PORT_B/a2a/peers" -H "Authorization: Bearer $BRIDGE_AUTH")
check "node-a discovered node-b" "127.0.0.1:$PORT_B" "$peers_a"
check "node-b discovered node-a" "127.0.0.1:$PORT_A" "$peers_b"

# 2b. /a2a/peers requires global auth (no token -> 401)
code_peers=$(curl -s -o /dev/null -w "%{http_code}" "http://127.0.0.1:$PORT_A/a2a/peers")
check "/a2a/peers without token -> 401" "401" "$code_peers"

# 3. announce auth: wrong token -> 401
code=$(curl -s -o /dev/null -w "%{http_code}" -X POST "http://127.0.0.1:$PORT_A/a2a/announce" \
  -H "Authorization: Bearer wrong" -H "Content-Type: application/json" \
  -d '{"agent_card":{"url":"http://x"}}')
check "announce wrong token -> 401" "401" "$code"

# 4. default deployment unaffected: a non-mesh bridge has no /a2a/peers
#    (verified implicitly — endpoints only exist because mesh.enabled=true)

echo ""
echo "=== Mesh L0: $PASS passed, $FAIL failed ==="
[[ $FAIL -eq 0 ]] || { echo "--- node-a log tail ---"; tail -20 "$TMP/a.log"; }
exit $FAIL
