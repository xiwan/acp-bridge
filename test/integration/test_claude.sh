#!/bin/bash
# Claude agent 测试
set -uo pipefail
source "$(dirname "$0")/../lib.sh"

echo "=== Claude 测试 ==="

echo "--- 同步调用 ---"
resp=$("$CLIENT" -a claude "回复ok两个字就行" 2>/dev/null)
run_test "同步调用有回复" "ok\|OK" "$resp"

echo ""
echo "--- 流式调用 ---"
resp=$("$CLIENT" --stream -a claude "回复ok两个字就行" 2>/dev/null)
run_test "流式调用有输出" "ok\|OK" "$resp"

echo ""
echo "--- 多轮对话 ---"
SESSION="00000000-0000-0000-0000-cc0000000001"
resp1=$("$CLIENT" -a claude -s "$SESSION" "My favorite fruit is pineapple. What do you think about pineapples?" 2>/dev/null)
run_test "多轮第1轮有回复" "pineapple\|Pineapple\|fruit\|great\|delicious\|tropical" "$resp1"

resp2=$("$CLIENT" -a claude -s "$SESSION" "What is my favorite fruit? Reply with only the fruit name" 2>/dev/null)
resp2_joined=$(echo "$resp2" | tr -d '\n')
run_test "多轮第2轮记住上下文" "pineapple\|Pineapple" "$resp2_joined"

echo ""
echo "--- 异步任务 ---"
ASYNC_SESSION="00000000-0000-0000-0000-cc0000000002"
job_id=$("$CLIENT" --async -a claude -s "$ASYNC_SESSION" "回复ok两个字就行" 2>&1 1>/dev/null | grep job_id | sed 's/job_id: //')
if [[ -n "$job_id" ]]; then
    echo "  等待任务完成 (10s)..."
    sleep 10
    resp=$("$CLIENT" --job-status "$job_id" 2>/dev/null)
    run_test "异步任务查询有结果" "ok\|OK\|completed\|claude" "$resp"
else
    echo "❌ 异步任务未返回 job_id"
    ((FAIL++))
fi

print_summary "Claude"
