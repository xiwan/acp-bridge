#!/bin/bash
# 公共测试 — 基础接口 + 错误处理
set -uo pipefail
source "$(dirname "$0")/../lib.sh"

echo "=== 公共测试 ==="

echo "--- 基础接口 ---"
resp=$("$CLIENT" -l 2>&1)
run_test "列出 agents 包含 kiro" "kiro" "$resp"
run_test "列出 agents 包含 codex" "codex" "$resp"

echo ""
echo "--- 错误处理 ---"
expect_fail "不存在的 agent" "$CLIENT" -a nonexistent_agent_xyz "hi"
expect_fail "无效 bridge 地址" env ACP_BRIDGE_URL=http://127.0.0.1:19999 "$CLIENT" "hi"

print_summary "公共"
