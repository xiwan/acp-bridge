#!/bin/bash
# Harness Factory agent 测试
set -uo pipefail
source "$(dirname "$0")/lib.sh"

echo "=== Harness Factory 测试 ==="

echo "--- 同步调用 ---"
resp=$("$CLIENT" -a harness "Reply with only the word ok" 2>&1)
run_test "同步调用有回复" "ok\|OK" "$resp"

echo ""
echo "--- 流式调用 ---"
resp=$("$CLIENT" --stream -a harness "Reply with only the word ok" 2>&1)
run_test "流式调用有输出" "ok\|OK" "$resp"

echo ""
echo "--- Tool 调用 ---"
resp=$("$CLIENT" -a harness "What is today's date? Use the shell tool to run 'date'. Reply with only the date." 2>&1)
run_test "Tool 调用 (shell date)" "2026\|Apr\|tool" "$resp"

resp=$("$CLIENT" -a harness "List files in /tmp/hf using the fs tool. Reply briefly." 2>&1)
run_test "Tool 调用 (fs list)" "tool\|fs_list\|empty\|files" "$resp"

echo ""
echo "--- 多轮对话 ---"
SESSION="00000000-0000-0000-0000-c00000000001"
resp1=$("$CLIENT" -a harness -s "$SESSION" "Remember the secret code is mango. Reply only with understood." 2>&1)
run_test "多轮第1轮有回复" "understood\|ok\|OK\|Understood" "$resp1"

resp2=$("$CLIENT" -a harness -s "$SESSION" "What is the secret code? Reply with only the code." 2>&1)
run_test "多轮第2轮记住上下文" "mango" "$resp2"

print_summary "Harness Factory"
