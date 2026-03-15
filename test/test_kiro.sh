#!/bin/bash
# Kiro agent 测试
set -uo pipefail
source "$(dirname "$0")/lib.sh"

echo "=== Kiro 测试 ==="

echo "--- 同步调用 ---"
resp=$("$CLIENT" -a kiro "回复ok两个字就行" 2>&1)
run_test "同步调用有回复" "ok" "$resp"

resp=$("$CLIENT" -a kiro "回复数字42就行" 2>&1)
run_test "同步返回内容" "42" "$resp"

echo ""
echo "--- 流式调用 ---"
resp=$("$CLIENT" --stream -a kiro "回复ok两个字就行" 2>&1)
run_test "流式调用有输出" "ok" "$resp"

echo ""
echo "--- 多轮对话 ---"
SESSION="00000000-0000-0000-0000-000000000099"
resp1=$("$CLIENT" -a kiro -s "$SESSION" "记住暗号是 pineapple，只回复 understood" 2>/dev/null)
run_test "多轮第1轮有回复" "understood\|ok\|记住" "$resp1"

resp2=$("$CLIENT" -a kiro -s "$SESSION" "暗号是什么？只回复暗号本身" 2>/dev/null)
resp2_joined=$(echo "$resp2" | tr -d '\n')
run_test "多轮第2轮记住上下文" "pineapple" "$resp2_joined"

echo ""
echo "--- 异步任务 ---"
resp=$("$CLIENT" --async -a kiro -s "00000000-0000-0000-0000-000000000087" "回复ok两个字就行" 2>/dev/null)
run_test "异步提交返回已提交" "已提交" "$resp"

ASYNC_SESSION="00000000-0000-0000-0000-000000000088"
job_id=$("$CLIENT" --async -a kiro -s "$ASYNC_SESSION" "回复数字42就行" 2>&1 1>/dev/null | grep job_id | sed 's/job_id: //')
if [[ -n "$job_id" ]]; then
    echo "  等待任务完成 (10s)..."
    sleep 10
    resp=$("$CLIENT" --job-status "$job_id" 2>/dev/null)
    run_test "异步任务查询有结果" "42\|completed\|kiro" "$resp"
else
    echo "❌ 异步任务未返回 job_id"
    ((FAIL++))
fi

print_summary "Kiro"
