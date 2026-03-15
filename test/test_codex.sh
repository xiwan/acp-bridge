#!/bin/bash
# Codex agent 测试
set -uo pipefail
source "$(dirname "$0")/lib.sh"

echo "=== Codex 测试 ==="

echo "--- 同步调用 ---"
resp=$("$CLIENT" -a codex "Reply with only the word ok" 2>&1)
run_test "同步调用有回复" "ok\|OK" "$resp"

resp=$("$CLIENT" -a codex "Reply with only the number 42" 2>&1)
run_test "同步返回内容" "42" "$resp"

echo ""
echo "--- 流式调用 ---"
resp=$("$CLIENT" --stream -a codex "Reply with only the word ok" 2>&1)
run_test "流式调用有输出" "ok\|OK" "$resp"

print_summary "Codex"
