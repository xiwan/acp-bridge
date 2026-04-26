#!/bin/bash
# 场景4 — 异步任务：提交、查询状态、等待完成
# 对应 SKILL.md Step 1: ">60s / long task → Async job" + references/async-jobs.md
set -uo pipefail
source "$(dirname "$0")/../lib.sh"

TOKEN="${ACP_TOKEN:-${ACP_BRIDGE_TOKEN:-}}"
BASE="$ACP_BRIDGE_URL"
AUTH=(-H "Authorization: Bearer $TOKEN")

echo "=== 场景: 异步任务 ==="

# --- 1. 通过 client 提交异步任务 ---
echo "--- 1. Client --async 提交 ---"
job_id=$("$CLIENT" --async -a kiro "回复 async-ok 就行" 2>&1 1>/dev/null | grep job_id | sed 's/job_id: //')
stdout=$("$CLIENT" --async -a kiro "回复 async-ok 就行" 2>/dev/null)
run_test "client 输出包含 Submitted" "Submitted\|已提交" "$stdout"
run_test "stderr 返回 job_id" "." "$job_id"

# --- 2. 通过 API 直接提交 ---
echo ""
echo "--- 2. API 直接提交 ---"
resp=$(curl -sf --max-time 30 -X POST "${AUTH[@]}" "$BASE/jobs" \
    -H "Content-Type: application/json" \
    -d '{"agent_name":"kiro","prompt":"回复数字 99 就行"}')
api_job_id=$(echo "$resp" | jq -r '.job_id // empty')
api_status=$(echo "$resp" | jq -r '.status // empty')
run_test "API 返回 job_id" "." "$api_job_id"
run_test "初始状态为 pending" "pending" "$api_status"

# --- 3. 查询状态变化 ---
echo ""
echo "--- 3. 等待完成并查询 ---"
echo "  等待 15s..."
sleep 15

if [[ -n "$api_job_id" ]]; then
    resp=$(curl -sf --max-time 10 "${AUTH[@]}" "$BASE/jobs/$api_job_id")
    final_status=$(echo "$resp" | jq -r '.status // empty')
    run_test "任务最终状态" "completed\|failed\|running" "$final_status"

    if [[ "$final_status" == "completed" ]]; then
        result=$(echo "$resp" | jq -r '.result // empty')
        run_test "完成的任务有结果" "99" "$result"
    fi
fi

# --- 4. client --job-status 查询 ---
echo ""
echo "--- 4. Client --job-status 查询 ---"
if [[ -n "$api_job_id" ]]; then
    resp=$("$CLIENT" --job-status "$api_job_id" 2>&1)
    run_test "client 查询有输出" "99\|kiro\|Status\|❌" "$resp"
fi

# --- 5. Job 列表 ---
echo ""
echo "--- 5. Job 列表 ---"
resp=$(curl -sf --max-time 10 "${AUTH[@]}" "$BASE/jobs")
run_test "列表返回 jobs 数组" "jobs" "$resp"
run_test "列表有 summary 统计" "summary" "$resp"

# --- 6. 不存在的 job ---
echo ""
echo "--- 6. 错误处理 ---"
resp=$(curl -s --max-time 10 "${AUTH[@]}" "$BASE/jobs/nonexistent-job-id-xyz")
run_test "不存在的 job 返回错误" "not found\|error\|404" "$resp"

print_summary "场景: 异步任务"
