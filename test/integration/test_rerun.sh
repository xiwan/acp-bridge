#!/bin/bash
# Pipeline rerun 集成测试
set -uo pipefail
source "$(dirname "$0")/../lib.sh"

BASE="$ACP_BRIDGE_URL"
AUTH="Authorization: Bearer $ACP_TOKEN"

_post() { curl -s -X POST "$BASE$1" -H "$AUTH" -H "Content-Type: application/json" -d "$2"; }
_get()  { curl -s "$BASE$1" -H "$AUTH"; }

_wait_pipeline() {
    local pid="$1" max="${2:-120}" interval="${3:-5}"
    for i in $(seq 1 $((max / interval))); do
        status=$(_get "/pipelines/$pid" | python3 -c "import sys,json; print(json.load(sys.stdin).get('status',''))" 2>/dev/null)
        if [[ "$status" == "completed" || "$status" == "failed" ]]; then
            echo "$status"; return
        fi
        sleep "$interval"
    done
    echo "timeout"
}

echo "=== Pipeline Rerun Tests ==="

# 1. Submit original pipeline
echo ""
echo "--- Test 1: Full rerun ---"
RESP=$(_post "/pipelines" '{"mode":"sequence","steps":[{"agent":"harness","prompt":"Write file rerun_test.txt with content ORIGINAL in the cwd. Reply done.","timeout":120}]}')
PID=$(echo "$RESP" | python3 -c "import sys,json; print(json.load(sys.stdin)['pipeline_id'])")
run_test "原始 pipeline 提交" "pipeline_id" "$RESP"

STATUS=$(_wait_pipeline "$PID" 120)
run_test "原始 pipeline 完成" "completed" "$STATUS"

# 2. Rerun it
RESP2=$(_post "/pipelines/$PID/rerun" '{}')
run_test "rerun 提交成功" "rerun_from" "$RESP2"
PID2=$(echo "$RESP2" | python3 -c "import sys,json; print(json.load(sys.stdin)['pipeline_id'])")

STATUS2=$(_wait_pipeline "$PID2" 120)
run_test "rerun pipeline 完成" "completed" "$STATUS2"

# Verify shared_cwd is inherited
CWD1=$(_get "/pipelines/$PID" | python3 -c "import sys,json; print(json.load(sys.stdin)['shared_cwd'])")
CWD2=$(_get "/pipelines/$PID2" | python3 -c "import sys,json; print(json.load(sys.stdin)['shared_cwd'])")
run_test "shared_cwd 复用" "$CWD1" "$CWD2"

# 3. Rerun with prompt_override
echo ""
echo "--- Test 2: Rerun with prompt_override ---"
RESP3=$(_post "/pipelines/$PID/rerun" '{"prompt_override":"Instead write MODIFIED to the file"}')
run_test "prompt_override rerun 提交" "rerun_from" "$RESP3"
PID3=$(echo "$RESP3" | python3 -c "import sys,json; print(json.load(sys.stdin)['pipeline_id'])")
STATUS3=$(_wait_pipeline "$PID3" 120)
run_test "prompt_override rerun 完成" "completed" "$STATUS3"

# 4. Error: rerun a running pipeline (we'll test with a non-existent one)
echo ""
echo "--- Test 3: Error cases ---"
ERR=$(_post "/pipelines/nonexistent-id/rerun" '{}')
run_test "不存在的 pipeline 返回错误" "not found" "$ERR"

# 5. Error: rerun conversation mode
RESP_CONV=$(_post "/pipelines" '{"mode":"conversation","participants":["kiro","claude"],"topic":"test","config":{"max_turns":1}}')
PID_CONV=$(echo "$RESP_CONV" | python3 -c "import sys,json; print(json.load(sys.stdin).get('pipeline_id',''))" 2>/dev/null)
if [[ -n "$PID_CONV" ]]; then
    sleep 15
    ERR_CONV=$(_post "/pipelines/$PID_CONV/rerun" '{}')
    run_test "conversation 模式不支持 rerun" "not supported" "$ERR_CONV"
fi

# 6. from_step out of range
echo ""
ERR_STEP=$(_post "/pipelines/$PID/rerun" '{"from_step": 99}')
run_test "from_step 越界返回错误" "out of range" "$ERR_STEP"

print_summary "Pipeline Rerun"
