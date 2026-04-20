#!/bin/bash
# Trae Agent 测试 — PTY 模式，通过 LiteLLM/Bedrock
set -uo pipefail
source "$(dirname "$0")/lib.sh"

echo "=== Trae 测试 ==="

# --- 前置检查 ---
if ! curl -s --max-time 3 "$ACP_BRIDGE_URL/health" -H "Authorization: Bearer ${ACP_TOKEN:-}" | python3 -c "import sys,json; agents=[a['name'] for a in json.load(sys.stdin).get('agents',[])]; sys.exit(0 if 'trae' in agents else 1)" 2>/dev/null; then
    echo "⚠️  trae agent 未注册，跳过"
    SKIP=1
    print_summary "Trae"
    exit 0
fi

TOKEN="${ACP_TOKEN:-${ACP_BRIDGE_TOKEN:-}}"
AUTH="Authorization: Bearer $TOKEN"

echo "--- 同步调用 ---"
resp=$(curl -s --max-time 120 -X POST "$ACP_BRIDGE_URL/runs" \
    -H "$AUTH" -H "Content-Type: application/json" \
    -d '{"agent_name":"trae","input":[{"parts":[{"content":"回复ok两个字就行","content_type":"text/plain"}]}]}')
run_test "同步调用有回复" "completed\|ok\|result" "$resp"

echo ""
echo "--- 异步任务 ---"
resp=$(curl -s --max-time 10 -X POST "$ACP_BRIDGE_URL/jobs" \
    -H "$AUTH" -H "Content-Type: application/json" \
    -d '{"agent_name":"trae","prompt":"回复hello就行","session_id":"test-trae-001"}')
job_id=$(echo "$resp" | python3 -c "import sys,json; print(json.load(sys.stdin).get('job_id',''))" 2>/dev/null)
run_test "异步提交返回 job_id" "[0-9a-f-]" "$job_id"

if [[ -n "$job_id" ]]; then
    echo "  等待任务完成 (max 120s)..."
    for i in $(seq 1 12); do
        sleep 10
        status=$(curl -s "$ACP_BRIDGE_URL/jobs/$job_id" -H "$AUTH" | python3 -c "import sys,json; print(json.load(sys.stdin).get('status',''))" 2>/dev/null)
        [[ "$status" == "completed" || "$status" == "failed" ]] && break
    done
    result=$(curl -s "$ACP_BRIDGE_URL/jobs/$job_id" -H "$AUTH")
    run_test "异步任务完成" "completed\|failed" "$status"
    run_test "异步任务有结果" "result\|hello" "$result"
fi

print_summary "Trae"
