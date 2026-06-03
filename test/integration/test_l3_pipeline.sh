#!/bin/bash
# Integration test — A2A Mesh L3a: cross-Bridge pipeline + S3 workspace relay.
# Two bridges: A (originator, no `filewriter`) seeds B (has `filewriter`, writes a
# file into its cwd). A runs a sequence pipeline whose step targets `filewriter`;
# L3 relays A's shared_cwd to B via S3, B writes a file in the unpacked workspace,
# and the file is merged back into A's shared_cwd.
#
# HARD PREREQUISITE: S3 must be available. If not, the test SKIPS (not fails),
# because L3 depends on shared S3 by design.
#
# Usage: bash test/integration/test_l3_pipeline.sh
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"

# S3 gate: skip cleanly if no S3 (L3's hard prerequisite).
if ! uv run python -c "from src import s3; import sys; sys.exit(0 if s3.init() else 1)" 2>/dev/null; then
  echo "⏭️  SKIP: S3 unavailable — L3 requires shared S3 (by design)."
  exit 0
fi

PORT_A=18096
PORT_B=18097
BRIDGE_AUTH="l3-test"
MESH_AUTH="l3-mesh"
TMP="$(mktemp -d)"
WS_A="$TMP/ws-a"; mkdir -p "$WS_A"
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

# A tiny ACP agent that writes a file into its cwd and reports the prompt.
cat > "$TMP/filewriter.py" <<'PY'
import json, sys, os, uuid
def send(m): print(json.dumps(m), flush=True)
sessions = {}
for line in sys.stdin:
    line = line.strip()
    if not line: continue
    try: msg = json.loads(line)
    except: continue
    m, p, rid = msg.get("method",""), msg.get("params") or {}, msg.get("id")
    if m == "initialize":
        send({"jsonrpc":"2.0","id":rid,"result":{"protocolVersion":1,"agentInfo":{"name":"filewriter","version":"1"},"capabilities":{}}})
    elif m == "session/new":
        sid = str(uuid.uuid4()); sessions[sid] = p.get("cwd","/tmp")
        send({"jsonrpc":"2.0","id":rid,"result":{"sessionId":sid}})
    elif m == "session/prompt":
        sid = p.get("sessionId",""); cwd = sessions.get(sid, "/tmp")
        text = "".join(x.get("text","") for x in p.get("prompt",[]) if x.get("type")=="text")
        with open(os.path.join(cwd, "built.txt"), "w") as f: f.write("BUILT:"+text)
        send({"jsonrpc":"2.0","method":"session/update","params":{"sessionId":sid,"update":{"sessionUpdate":"agent_message_chunk","content":{"text":"wrote built.txt"}}}})
        send({"jsonrpc":"2.0","id":rid,"result":{"sessionId":sid,"stopReason":"end_turn"}})
    elif m == "ping" and rid is not None:
        send({"jsonrpc":"2.0","id":rid,"result":{}})
PY

make_config() {  # $1=node $2=port $3=seed_port $4=with_filewriter
  {
    echo "server: {host: \"127.0.0.1\", port: $2}"
    echo "security: {auth_token: \"$BRIDGE_AUTH\", allowed_ips: []}"
    echo "s3: {}"
    echo "agents:"
    if [[ "$4" == "yes" ]]; then
      echo "  filewriter:"
      echo "    enabled: true"
      echo "    mode: \"acp\""
      echo "    command: \"python\""
      echo "    acp_args: [\"$TMP/filewriter.py\"]"
      echo "    description: \"writes built.txt into cwd\""
    else
      echo "  echo:"
      echo "    enabled: true"
      echo "    mode: \"acp\""
      echo "    command: \"python\""
      echo "    acp_args: [\"$ROOT/examples/echo-agent.py\"]"
      echo "    description: \"local echo\""
    fi
    echo "mesh:"
    echo "  enabled: true"
    echo "  node_id: \"$1\""
    echo "  self_url: \"http://127.0.0.1:$2\""
    echo "  announce_interval: 3"
    echo "  token: \"$MESH_AUTH\""
    echo "  seeds: [\"http://127.0.0.1:$3\"]"
  } > "$TMP/config-$1.yaml"
}

echo "=== A2A Mesh L3a 跨节点 pipeline 集成测试 ==="
make_config node-a "$PORT_A" "$PORT_B" no
make_config node-b "$PORT_B" "$PORT_A" yes

uv run python main.py --config "$TMP/config-node-a.yaml" >"$TMP/a.log" 2>&1 & PIDS+=($!)
uv run python main.py --config "$TMP/config-node-b.yaml" >"$TMP/b.log" 2>&1 & PIDS+=($!)

for i in $(seq 1 30); do
  curl -s "http://127.0.0.1:$PORT_A/.well-known/agent.json" >/dev/null 2>&1 && \
  curl -s "http://127.0.0.1:$PORT_B/.well-known/agent.json" >/dev/null 2>&1 && break
  sleep 1
done
sleep 8  # discovery + reconcile so A knows filewriter is remote

AUTH=(-H "Authorization: Bearer $BRIDGE_AUTH")

# A runs a pipeline: single step targeting filewriter (remote on B), shared_cwd=WS_A
resp=$(curl -s -X POST "${AUTH[@]}" "http://127.0.0.1:$PORT_A/pipelines" \
  -H "Content-Type: application/json" \
  -d "{\"mode\":\"sequence\",\"steps\":[{\"agent\":\"filewriter\",\"prompt\":\"make it\",\"timeout\":120}],\"context\":{\"shared_cwd\":\"$WS_A\"}}")
pid=$(echo "$resp" | python3 -c "import sys,json; print(json.load(sys.stdin).get('pipeline_id',''))" 2>/dev/null)
check "pipeline submitted" "." "$pid"

# poll for completion
for i in $(seq 1 40); do
  st=$(curl -s "${AUTH[@]}" "http://127.0.0.1:$PORT_A/pipelines/$pid" | \
       python3 -c "import sys,json; print(json.load(sys.stdin).get('status',''))" 2>/dev/null)
  [[ "$st" == "completed" || "$st" == "failed" ]] && break
  sleep 2
done
check "pipeline completed" "completed" "$st"

# The file B wrote in the relayed workspace must be merged back into A's shared_cwd.
if [[ -f "$WS_A/built.txt" ]]; then
  content=$(cat "$WS_A/built.txt")
  # proves: B executed in the relayed workspace AND its output merged back to A.
  check "workspace merged back to A (built.txt present)" "BUILT:" "$content"
  check "B saw the relayed prompt" "make it" "$content"
else
  echo "❌ workspace merged back to A (built.txt) — file missing on A"; ((FAIL++))
fi

echo ""
echo "=== Mesh L3a: $PASS passed, $FAIL failed ==="
[[ $FAIL -eq 0 ]] || { echo "--- A log ---"; tail -30 "$TMP/a.log"; echo "--- B log ---"; tail -20 "$TMP/b.log"; }
exit $FAIL
