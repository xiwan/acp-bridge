#!/bin/bash
# Integration test — A2A Mesh L2 routing (A2A Client + remote agent invocation).
# Bridge A has NO echo agent; Bridge B has echo. With mesh, A discovers B, registers
# a remote `echo` handler, and a /runs call to A for `echo` is routed to B.
# Also verifies local-priority and that listing surfaces the remote agent.
#
# Usage: bash test/integration/test_l2_routing.sh
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"

PORT_A=18094
PORT_B=18095
BRIDGE_AUTH="l2-test"
MESH_AUTH="l2-mesh"
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

# Bridge B: has echo agent. Bridge A: no agents of its own, seeds=B.
make_config() {  # $1=node $2=port $3=seed_port $4=with_echo(yes/no)
  {
    echo "server:"
    echo "  host: \"127.0.0.1\""
    echo "  port: $2"
    echo "security:"
    echo "  auth_token: \"$BRIDGE_AUTH\""
    echo "  allowed_ips: []"
    echo "agents:"
    if [[ "$4" == "yes" ]]; then
      echo "  echo:"
      echo "    enabled: true"
      echo "    mode: \"acp\""
      echo "    command: \"python\""
      echo "    acp_args: [\"$ROOT/examples/echo-agent.py\"]"
      echo "    working_dir: \"/tmp\""
      echo "    description: \"Echo reference agent\""
    else
      echo "  placeholder:"
      echo "    enabled: true"
      echo "    mode: \"acp\""
      echo "    command: \"python\""
      echo "    acp_args: [\"$ROOT/examples/echo-agent.py\"]"
      echo "    working_dir: \"/tmp\""
      echo "    description: \"placeholder local agent\""
    fi
    echo "mesh:"
    echo "  enabled: true"
    echo "  node_id: \"$1\""
    echo "  self_url: \"http://127.0.0.1:$2\""
    echo "  announce_interval: 3"
    echo "  token: \"$MESH_AUTH\""
    echo "  seeds:"
    echo "    - \"http://127.0.0.1:$3\""
  } > "$TMP/config-$1.yaml"
}

echo "=== A2A Mesh L2 路由集成测试 ==="
make_config node-a "$PORT_A" "$PORT_B" no    # A: no echo, seeds B
make_config node-b "$PORT_B" "$PORT_A" yes   # B: has echo, seeds A

uv run python main.py --config "$TMP/config-node-a.yaml" >"$TMP/a.log" 2>&1 & PIDS+=($!)
uv run python main.py --config "$TMP/config-node-b.yaml" >"$TMP/b.log" 2>&1 & PIDS+=($!)

# wait for both to bind
for i in $(seq 1 30); do
  ca=$(curl -s "http://127.0.0.1:$PORT_A/.well-known/agent.json" 2>/dev/null)
  cb=$(curl -s "http://127.0.0.1:$PORT_B/.well-known/agent.json" 2>/dev/null)
  [[ -n "$ca" && -n "$cb" ]] && break
  sleep 1
done
# let announce_loop (interval=3) discover peers + reconcile remote handlers
sleep 8

AUTH=(-H "Authorization: Bearer $BRIDGE_AUTH")

# 1. A discovered B's echo and registered it as a remote agent (appears in /agents)
agents_a=$(curl -s "${AUTH[@]}" "http://127.0.0.1:$PORT_A/agents")
check "A lists remote echo (via mesh)" '"echo"' "$agents_a"

# 1b. remote echo carries B's real description + a readable location suffix
echo_desc=$(echo "$agents_a" | python3 -c "import sys,json;print(next((a['description'] for a in json.load(sys.stdin)['agents'] if a['name']=='echo'),''))" 2>/dev/null)
check "remote echo desc has real text + location" 'Echo reference agent (via mesh@node-b)' "$echo_desc"

# 1c. remote echo carries structured location tags (mesh + node:<name>)
echo_tags=$(echo "$agents_a" | python3 -c "import sys,json;print((next((a for a in json.load(sys.stdin)['agents'] if a['name']=='echo'),{}).get('metadata') or {}).get('tags'))" 2>/dev/null)
check "remote echo tagged mesh" 'mesh' "$echo_tags"
check "remote echo tagged node:node-b" 'node:node-b' "$echo_tags"

# 1d. local placeholder on A is tagged 'local'
ph_tags=$(echo "$agents_a" | python3 -c "import sys,json;print((next((a for a in json.load(sys.stdin)['agents'] if a['name']=='placeholder'),{}).get('metadata') or {}).get('tags'))" 2>/dev/null)
check "local placeholder tagged local" 'local' "$ph_tags"

# 2. Calling echo on A is routed to B (A has no local echo)
resp=$(curl -s -X POST "${AUTH[@]}" "http://127.0.0.1:$PORT_A/runs" \
  -H "Content-Type: application/json" \
  -d '{"agent_name":"echo","input":[{"parts":[{"content":"routed hi","content_type":"text/plain"}]}]}')
check "A routes echo -> B (echoes prompt)" 'echo: routed hi' "$resp"

# 3. local-priority: B serves its own echo locally (no routing back to A)
resp_b=$(curl -s -X POST "${AUTH[@]}" "http://127.0.0.1:$PORT_B/runs" \
  -H "Content-Type: application/json" \
  -d '{"agent_name":"echo","input":[{"parts":[{"content":"local hi","content_type":"text/plain"}]}]}')
check "B serves echo locally" 'echo: local hi' "$resp_b"

echo ""
echo "=== Mesh L2: $PASS passed, $FAIL failed ==="
[[ $FAIL -eq 0 ]] || { echo "--- A log ---"; tail -25 "$TMP/a.log"; echo "--- B log ---"; tail -15 "$TMP/b.log"; }
exit $FAIL
