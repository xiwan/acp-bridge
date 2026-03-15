#!/bin/bash
# Claude agent 测试
set -uo pipefail
source "$(dirname "$0")/lib.sh"

echo "=== Claude 测试 ==="

echo "--- 同步调用 ---"
resp=$("$CLIENT" -a claude "回复ok两个字就行" 2>/dev/null)
run_test "同步调用有回复" "ok\|OK" "$resp"

echo ""
echo "--- 流式调用 ---"
resp=$("$CLIENT" --stream -a claude "回复ok两个字就行" 2>/dev/null)
run_test "流式调用有输出" "ok\|OK" "$resp"

print_summary "Claude"
