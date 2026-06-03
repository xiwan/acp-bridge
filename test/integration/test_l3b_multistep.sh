#!/bin/bash
# Integration test — A2A Mesh L3b: multi cross-node sequence chain + parallel isolation.
# Bridge B hosts two file agents: `appender` (reads existing files, adds its own) and
# `tagger` (writes a unique per-call file). A (no local agents of these) routes:
#   - SEQUENCE: local seed -> remote appender -> remote appender2, verify chaining.
#   - PARALLEL: two remote steps in their own subdirs, verify both outputs survive.
#
# HARD PREREQUISITE: S3. SKIPS cleanly if unavailable (L3 by design).
# Usage: bash test/integration/test_l3b_multistep.sh
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"

if ! uv run python -c "from src import s3; import sys; sys.exit(0 if s3.init() else 1)" 2>/dev/null; then
  echo "⏭️  SKIP: S3 unavailable — L3 requires shared S3 (by design)."
  exit 0
fi

PORT_A=18098
PORT_B=18099
BRIDGE_AUTH="l3b-test"
MESH_AUTH="l3b-mesh"
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

# Agent that appends a line to ledger.txt in its cwd (proves it saw prior content).
cat > "$TMP/appender.py" <<'PY'
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
        send({"jsonrpc":"2.0","id":rid,"result":{"protocolVersion":1,"agentInfo":{"name":"a","version":"1"},"capabilities":{}}})
    elif m == "session/new":
        sid=str(uuid.uuid4()); sessions[sid]=p.get("cwd","/tmp")
        send({"jsonrpc":"2.0","id":rid,"result":{"sessionId":sid}})
    elif m == "session/prompt":
        sid=p.get("sessionId",""); cwd=sessions.get(sid,"/tmp")
        text="".join(x.get("text","") for x in p.get("prompt",[]) if x.get("type")=="text")
        path=os.path.join(cwd,"ledger.txt")
        prior=open(path).read() if os.path.exists(path) else ""
        # Write the FULL prompt text so the test can assert on its distinctive keyword
        # (the pipeline decorates prompts, so we cannot rely on word position).
        open(path,"w").write(prior+"PROMPT<"+text.replace("\n"," ")+">\n")
        send({"jsonrpc":"2.0","method":"session/update","params":{"sessionId":sid,"update":{"sessionUpdate":"agent_message_chunk","content":{"text":"appended"}}}})
        send({"jsonrpc":"2.0","id":rid,"result":{"sessionId":sid,"stopReason":"end_turn"}})
    elif m == "ping" and rid is not None:
        send({"jsonrpc":"2.0","id":rid,"result":{}})
PY

make_config() {  # $1 node $2 port $3 seed $4 with_agents(yes/no)
  {
    echo "server: {host: \"127.0.0.1\", port: $2}"
    echo "security: {auth_token: \"$BRIDGE_AUTH\", allowed_ips: []}"
    echo "s3: {}"
    echo "agents:"
    if [[ "$4" == "yes" ]]; then
      for a in appender tagger; do
        echo "  $a: {enabled: true, mode: \"acp\", command: \"python\", acp_args: [\"$TMP/appender.py\"], description: \"$a\"}"
      done
    else
      echo "  echo: {enabled: true, mode: \"acp\", command: \"python\", acp_args: [\"$ROOT/examples/echo-agent.py\"], description: \"echo\"}"
    fi
    echo "mesh: {enabled: true, node_id: \"$1\", self_url: \"http://127.0.0.1:$2\", announce_interval: 3, token: \"$MESH_AUTH\", seeds: [\"http://127.0.0.1:$3\"]}"
  } > "$TMP/config-$1.yaml"
}

echo "=== A2A Mesh L3b 多步/并行 集成测试 ==="
make_config node-a "$PORT_A" "$PORT_B" no
make_config node-b "$PORT_B" "$PORT_A" yes
uv run python main.py --config "$TMP/config-node-a.yaml" >"$TMP/a.log" 2>&1 & PIDS+=($!)
uv run python main.py --config "$TMP/config-node-b.yaml" >"$TMP/b.log" 2>&1 & PIDS+=($!)
for i in $(seq 1 30); do
  curl -s "http://127.0.0.1:$PORT_A/.well-known/agent.json" >/dev/null 2>&1 && \
  curl -s "http://127.0.0.1:$PORT_B/.well-known/agent.json" >/dev/null 2>&1 && break
  sleep 1
done
sleep 8
AUTH=(-H "Authorization: Bearer $BRIDGE_AUTH")

run_pipeline() {  # $1=json body -> echoes final shared_cwd via artifacts list; sets PID_OUT/ST_OUT
  local body="$1"
  local resp pid st
  resp=$(curl -s -X POST "${AUTH[@]}" "http://127.0.0.1:$PORT_A/pipelines" -H "Content-Type: application/json" -d "$body")
  pid=$(echo "$resp" | python3 -c "import sys,json;print(json.load(sys.stdin).get('pipeline_id',''))" 2>/dev/null)
  for i in $(seq 1 40); do
    st=$(curl -s "${AUTH[@]}" "http://127.0.0.1:$PORT_A/pipelines/$pid" | python3 -c "import sys,json;print(json.load(sys.stdin).get('status',''))" 2>/dev/null)
    [[ "$st" == "completed" || "$st" == "failed" ]] && break
    sleep 2
  done
  echo "$st"
}

# --- Case 1: SEQUENCE chain across two remote steps ---
WS1="$TMP/ws-seq"; mkdir -p "$WS1"
st=$(run_pipeline "{\"mode\":\"sequence\",\"steps\":[{\"agent\":\"appender\",\"prompt\":\"add one\",\"timeout\":120},{\"agent\":\"appender\",\"prompt\":\"add two\",\"timeout\":120}],\"context\":{\"shared_cwd\":\"$WS1\"}}")
check "seq pipeline completed" "completed" "$st"
ledger=$(cat "$WS1/ledger.txt" 2>/dev/null || echo MISSING)
check "seq step1 output carried forward" "add one" "$ledger"
check "seq step2 saw step1 + appended" "add two" "$ledger"

# --- Case 2: PARALLEL two remote steps, isolated subdirs ---
WS2="$TMP/ws-par"; mkdir -p "$WS2"
st=$(run_pipeline "{\"mode\":\"parallel\",\"steps\":[{\"agent\":\"appender\",\"prompt\":\"p alpha\",\"timeout\":120},{\"agent\":\"tagger\",\"prompt\":\"p beta\",\"timeout\":120}],\"context\":{\"shared_cwd\":\"$WS2\"}}")
check "parallel pipeline completed" "completed" "$st"
# parallel gives each step its own subdir; both outputs must survive (no overwrite)
a_out=$(cat "$WS2/appender/ledger.txt" 2>/dev/null || echo MISSING)
t_out=$(cat "$WS2/tagger/ledger.txt" 2>/dev/null || echo MISSING)
check "parallel appender output survived" "p alpha" "$a_out"
check "parallel tagger output survived"   "p beta"  "$t_out"

echo ""
echo "=== Mesh L3b: $PASS passed, $FAIL failed ==="
[[ $FAIL -eq 0 ]] || { echo "--- A log ---"; tail -30 "$TMP/a.log"; echo "--- B log ---"; tail -20 "$TMP/b.log"; }
exit $FAIL
