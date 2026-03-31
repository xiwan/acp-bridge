#!/bin/bash
# Pipeline 集成测试 — sequence, parallel, race
set -uo pipefail
source "$(dirname "$0")/lib.sh"

TOKEN="${ACP_TOKEN:-$ACP_BRIDGE_TOKEN}"
BASE="${ACP_BRIDGE_URL:-http://127.0.0.1:18010}"
AUTH="Authorization: Bearer $TOKEN"

_post() { curl -s -X POST "$BASE$1" -H "$AUTH" -H "Content-Type: application/json" -d "$2"; }
_get()  { curl -s "$BASE$1" -H "$AUTH"; }

echo "=== Pipeline 测试 ==="

# --- Test 1: sequence mode ---
echo "--- Sequence 模式 ---"
resp=$(_post "/pipelines" '{
  "mode": "sequence",
  "steps": [
    {"agent": "kiro", "prompt": "只回复数字 42", "output_as": "num"},
    {"agent": "kiro", "prompt": "把 {{num}} 乘以 2，只回复结果数字"}
  ]
}')
pid=$(echo "$resp" | python3 -c "import sys,json; print(json.load(sys.stdin).get('pipeline_id',''))" 2>/dev/null)
run_test "sequence 提交成功" "pipeline_id" "$resp"

if [[ -n "$pid" ]]; then
    echo "  等待 pipeline 完成 (30s)..."
    sleep 30
    result=$(_get "/pipelines/$pid")
    run_test "sequence 状态 completed" "completed" "$result"
    run_test "sequence 有 steps" "steps" "$result"
fi

# --- Test 2: parallel mode ---
echo ""
echo "--- Parallel 模式 ---"
resp=$(_post "/pipelines" '{
  "mode": "parallel",
  "steps": [
    {"agent": "kiro", "prompt": "只回复 hello"},
    {"agent": "kiro", "prompt": "只回复 world"}
  ]
}')
pid=$(echo "$resp" | python3 -c "import sys,json; print(json.load(sys.stdin).get('pipeline_id',''))" 2>/dev/null)
run_test "parallel 提交成功" "pipeline_id" "$resp"

if [[ -n "$pid" ]]; then
    echo "  等待 pipeline 完成 (30s)..."
    sleep 30
    result=$(_get "/pipelines/$pid")
    run_test "parallel 状态 completed" "completed" "$result"
fi

# --- Test 3: race mode ---
echo ""
echo "--- Race 模式 ---"
resp=$(_post "/pipelines" '{
  "mode": "race",
  "steps": [
    {"agent": "kiro", "prompt": "只回复 fast"},
    {"agent": "kiro", "prompt": "只回复 slow"}
  ]
}')
pid=$(echo "$resp" | python3 -c "import sys,json; print(json.load(sys.stdin).get('pipeline_id',''))" 2>/dev/null)
run_test "race 提交成功" "pipeline_id" "$resp"

if [[ -n "$pid" ]]; then
    echo "  等待 pipeline 完成 (30s)..."
    sleep 30
    result=$(_get "/pipelines/$pid")
    run_test "race 状态 completed" "completed" "$result"
fi

# --- Test 4: list pipelines ---
echo ""
echo "--- 列表查询 ---"
resp=$(_get "/pipelines")
run_test "列表返回 pipelines" "pipelines" "$resp"

# --- Test 5: invalid mode ---
echo ""
echo "--- 错误处理 ---"
resp=$(_post "/pipelines" '{"mode": "invalid", "steps": [{"agent": "kiro", "prompt": "hi"}]}')
run_test "无效 mode 返回 400" "invalid mode" "$resp"

resp=$(_post "/pipelines" '{"mode": "sequence", "steps": []}')
run_test "空 steps 返回 400" "steps required" "$resp"

# --- Test 6: not found ---
resp=$(_get "/pipelines/nonexistent-id")
run_test "不存在的 pipeline 返回 404" "not found" "$resp"

print_summary "Pipeline"
