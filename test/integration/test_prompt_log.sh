#!/bin/bash
# Integration test — verify prompt_log persists final prompts across all paths.
# Requires a running Bridge (started after v0.21.3 changes loaded).
#
# Usage:
#   bash test/integration/test_prompt_log.sh
set -uo pipefail
source "$(dirname "$0")/../lib.sh"

TOKEN="${ACP_TOKEN:-${ACP_BRIDGE_TOKEN:-}}"
BASE="${ACP_BRIDGE_URL:-http://127.0.0.1:18010}"
AUTH="Authorization: Bearer $TOKEN"

_get() { curl -s "$BASE$1" -H "$AUTH"; }
_post() { curl -s -X POST "$BASE$1" -H "$AUTH" -H "Content-Type: application/json" -d "$2"; }

echo "=== prompt_log 集成测试 ==="

# --------------------------------------------------------------------
# 1. /admin/prompts endpoint reachable
# --------------------------------------------------------------------
echo ""
echo "--- 1. /admin/prompts 端点可达 ---"
admin_resp=$(_get "/admin/prompts?limit=1")
run_test "/admin/prompts 返回 JSON" "records" "$admin_resp"

# --------------------------------------------------------------------
# 2. Pipeline → records appear in /pipelines/{id}/prompts
# --------------------------------------------------------------------
echo ""
echo "--- 2. Pipeline 触发后 prompt 被记录 ---"
sub_resp=$(_post "/pipelines" '{
  "mode": "sequence",
  "steps": [
    {"agent": "kiro", "prompt": "Write hello-promptlog to current dir as plog.txt. Just create the file."}
  ]
}')
pid=$(echo "$sub_resp" | python3 -c "import sys,json; print(json.load(sys.stdin).get('pipeline_id',''))" 2>/dev/null)
run_test "pipeline 提交成功" "pipeline_id" "$sub_resp"

if [[ -n "$pid" ]]; then
    # Wait briefly for step to start (record happens at step start, not end)
    sleep 3
    prompts=$(_get "/pipelines/$pid/prompts?include=final")
    run_test "/pipelines/{id}/prompts 返回 records" "records" "$prompts"
    run_test "记录包含 agent kiro" "kiro" "$prompts"
    # Final must include the ws_hint header (English template)
    run_test "final 包含 shared_workspace 提示" "shared workspace" "$prompts"
    # Verify original prompt content survives
    run_test "final 包含原始 prompt 文本" "hello-promptlog" "$prompts"
fi

# --------------------------------------------------------------------
# 3. /pipelines/{id}/prompts default omits final field
# --------------------------------------------------------------------
echo ""
echo "--- 3. 默认响应不返回 final 字段 ---"
if [[ -n "$pid" ]]; then
    summary=$(_get "/pipelines/$pid/prompts")
    # Records should be there but `final` key should not appear at the record level
    has_records=$(echo "$summary" | python3 -c "import sys,json; d=json.load(sys.stdin); print('YES' if d.get('records') else 'NO')" 2>/dev/null)
    has_final=$(echo "$summary" | python3 -c "
import sys, json
d = json.load(sys.stdin)
recs = d.get('records', [])
print('YES' if recs and 'final' in recs[0] else 'NO')
" 2>/dev/null)
    run_test "默认响应有 records" "YES" "$has_records"
    run_test "默认响应不含 final 字段" "NO" "$has_final"
fi

# --------------------------------------------------------------------
# 4. /admin/prompts cross-cutting search by parent_type
# --------------------------------------------------------------------
echo ""
echo "--- 4. /admin/prompts 跨类型搜索 ---"
search=$(_get "/admin/prompts?parent_type=pipeline_step&limit=5")
run_test "搜索 pipeline_step 返回 records" "pipeline_step" "$search"

# --------------------------------------------------------------------
# 5. Job → records appear in /jobs/{id}/prompts
# --------------------------------------------------------------------
echo ""
echo "--- 5. Job 触发后 prompt 被记录 ---"
job_resp=$(_post "/jobs" '{
  "agent_name": "kiro",
  "prompt": "Reply with the word PROMPTLOG and stop."
}')
job_id=$(echo "$job_resp" | python3 -c "import sys,json; print(json.load(sys.stdin).get('job_id',''))" 2>/dev/null)
run_test "job 提交成功" "job_id" "$job_resp"

if [[ -n "$job_id" ]]; then
    sleep 3
    job_prompts=$(_get "/jobs/$job_id/prompts?include=final")
    run_test "/jobs/{id}/prompts 返回 records" "records" "$job_prompts"
    run_test "job 记录的 agent=kiro" "kiro" "$job_prompts"
    run_test "job final 含原始 prompt" "PROMPTLOG" "$job_prompts"
fi

# --------------------------------------------------------------------
# 6. Direct lookup by record_id
# --------------------------------------------------------------------
echo ""
echo "--- 6. /admin/prompts/{record_id} 直查 ---"
if [[ -n "$pid" ]]; then
    rec_id=$(_get "/pipelines/$pid/prompts" | python3 -c "
import sys, json
d = json.load(sys.stdin)
recs = d.get('records', [])
print(recs[0]['record_id'] if recs else '')
" 2>/dev/null)
    if [[ -n "$rec_id" ]]; then
        single=$(_get "/admin/prompts/$rec_id")
        run_test "单条查询返回 final 字段" "final" "$single"
    fi
fi

print_summary "prompt_log"
