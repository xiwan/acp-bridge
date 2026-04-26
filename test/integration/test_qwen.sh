#!/bin/bash
# Qwen Code agent 测试
set -uo pipefail
source "$(dirname "$0")/../lib.sh"

echo "=== Qwen 测试 ==="

echo "--- 同步调用 ---"
resp=$("$CLIENT" -a qwen "Reply with only the word ok" 2>&1)
run_test "同步调用有回复" "ok\|OK" "$resp"

resp=$("$CLIENT" -a qwen "Reply with only the number 42" 2>&1)
run_test "同步返回内容" "42" "$resp"

echo ""
echo "--- 流式调用 ---"
resp=$("$CLIENT" --stream -a qwen "Reply with only the word ok" 2>&1)
run_test "流式调用有输出" "ok\|OK" "$resp"

echo ""
echo "--- 多轮对话 ---"
SESSION="00000000-0000-0000-0000-a00000000001"
resp1=$("$CLIENT" -a qwen -s "$SESSION" "Remember the secret code is pineapple, reply only with understood" 2>&1)
run_test "多轮第1轮有回复" "understood\|ok\|OK\|记住" "$resp1"

resp2=$("$CLIENT" -a qwen -s "$SESSION" "What is the secret code? Reply with only the code" 2>&1)
run_test "多轮第2轮记住上下文" "pineapple" "$resp2"

echo ""
echo "--- 异步任务 ---"
ASYNC_SESSION="00000000-0000-0000-0000-a00000000002"
job_id=$("$CLIENT" --async -a qwen -s "$ASYNC_SESSION" "Reply with only the word ok" 2>&1 1>/dev/null | grep job_id | sed 's/job_id: //')
if [[ -n "$job_id" ]]; then
    echo "  等待任务完成 (10s)..."
    sleep 10
    resp=$("$CLIENT" --job-status "$job_id" 2>/dev/null)
    run_test "异步任务查询有结果" "ok\|OK\|completed\|qwen" "$resp"
else
    echo "❌ 异步任务未返回 job_id"
    ((FAIL++))
fi

print_summary "Qwen"
