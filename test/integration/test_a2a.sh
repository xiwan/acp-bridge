#!/bin/bash
# Integration test — A2A Mesh L1 (POST /a2a remote invocation).
# Spins up ONE bridge with mesh enabled + echo-agent (no real CLI needed), then
# verifies the /a2a JSON-RPC endpoint: tasks/send end-to-end, mesh.token auth,
# error codes, billing placeholders, and tasks/get. Self-contained.
#
# Usage: bash test/integration/test_a2a.sh
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"

PORT=18093
BRIDGE_AUTH="a2a-test"
MESH_AUTH="a2a-mesh"
URL="http://127.0.0.1:$PORT"
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

cat > "$TMP/config.yaml" <<EOF
server:
  host: "127.0.0.1"
  port: $PORT
security:
  auth_token: "$BRIDGE_AUTH"
  allowed_ips: []
agents:
  echo:
    enabled: true
    mode: "acp"
    command: "python"
    acp_args: ["$ROOT/examples/echo-agent.py"]
    working_dir: "/tmp"
    description: "Echo reference agent"
mesh:
  enabled: true
  node_id: "node-a2a"
  self_url: "$URL"
  announce_interval: 30
  token: "$MESH_AUTH"
  seeds: []
EOF

echo "=== A2A Mesh L1 集成测试 ==="
uv run python main.py --config "$TMP/config.yaml" >"$TMP/bridge.log" 2>&1 & PIDS+=($!)

for i in $(seq 1 30); do
  curl -s "$URL/.well-known/agent.json" >/dev/null 2>&1 && break
  sleep 1
done

A2A="$URL/a2a"
send_body='{"jsonrpc":"2.0","id":1,"method":"tasks/send","params":{"skill":"echo","message":{"parts":[{"type":"text","text":"hello a2a"}]}}}'

# 1. tasks/send end-to-end (echo agent -> "echo: hello a2a")
resp=$(curl -s -X POST "$A2A" -H "Authorization: Bearer $MESH_AUTH" \
  -H "Content-Type: application/json" -d "$send_body")
check "tasks/send completed"      '"state":"completed"' "$resp"
check "tasks/send echoes prompt"  'echo: hello a2a'      "$resp"
check "tasks/send cost free"      '"amount":0'           "$resp"

# 2. auth: missing mesh.token -> 401 (note: /a2a is exempt from global token)
code_noauth=$(curl -s -o /dev/null -w "%{http_code}" -X POST "$A2A" \
  -H "Content-Type: application/json" -d "$send_body")
check "no mesh.token -> 401" "401" "$code_noauth"

# 3. auth: wrong mesh.token -> 401
code_wrong=$(curl -s -o /dev/null -w "%{http_code}" -X POST "$A2A" \
  -H "Authorization: Bearer wrong" -H "Content-Type: application/json" -d "$send_body")
check "wrong mesh.token -> 401" "401" "$code_wrong"

# 4. unknown skill -> -32601
resp_skill=$(curl -s -X POST "$A2A" -H "Authorization: Bearer $MESH_AUTH" \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":2,"method":"tasks/send","params":{"skill":"ghost","message":{"parts":[]}}}')
check "unknown skill -> -32601" '"code":-32601' "$resp_skill"

# 5. unknown method -> -32601
resp_method=$(curl -s -X POST "$A2A" -H "Authorization: Bearer $MESH_AUTH" \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":3,"method":"tasks/dance","params":{}}')
check "unknown method -> -32601" '"code":-32601' "$resp_method"

# 6. parse error -> -32700, HTTP 400
resp_parse=$(curl -s -X POST "$A2A" -H "Authorization: Bearer $MESH_AUTH" \
  -H "Content-Type: application/json" -d 'not json')
check "parse error -> -32700" '"code":-32700' "$resp_parse"

# 7. tasks/get unknown id -> -32001
resp_get=$(curl -s -X POST "$A2A" -H "Authorization: Bearer $MESH_AUTH" \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":4,"method":"tasks/get","params":{"id":"nope"}}')
check "tasks/get unknown -> -32001" '"code":-32001' "$resp_get"

echo ""
echo "=== Mesh L1: $PASS passed, $FAIL failed ==="
[[ $FAIL -eq 0 ]] || { echo "--- bridge log tail ---"; tail -30 "$TMP/bridge.log"; }
exit $FAIL
