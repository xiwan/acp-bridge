#!/bin/bash
# ACP Bridge 全量测试 — 按 agent 类型分别执行
# 用法: bash test/test.sh [bridge_url]
#       bash test/test.sh http://127.0.0.1:18010 --only kiro
# 环境变量: ACP_TOKEN (必填)

set -uo pipefail

TEST_DIR="$(cd "$(dirname "$0")" && pwd)"
export ACP_BRIDGE_URL="${1:-http://127.0.0.1:18010}"

ONLY="${2:-}"
[[ "$ONLY" == "--only" ]] && ONLY="${3:-}" || true

TOTAL_PASS=0 TOTAL_FAIL=0 SUITES=0

run_suite() {
    local script="$1"
    local name
    name=$(basename "$script" .sh | sed 's/test_//')

    if [[ -n "$ONLY" && "$name" != "$ONLY" ]]; then
        return
    fi

    echo ""
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    bash "$script"
    local rc=$?

    # 从子脚本的输出中提取通过/失败数
    ((SUITES++))
    if [[ $rc -ne 0 ]]; then
        ((TOTAL_FAIL += rc))
    fi
}

echo "╔══════════════════════════════════╗"
echo "║   ACP Bridge 测试套件            ║"
echo "║   Bridge: $ACP_BRIDGE_URL"
echo "╚══════════════════════════════════╝"

run_suite "$TEST_DIR/test_common.sh"
run_suite "$TEST_DIR/test_tools.sh"
run_suite "$TEST_DIR/test_jobs.sh"
run_suite "$TEST_DIR/test_kiro.sh"
run_suite "$TEST_DIR/test_claude.sh"
run_suite "$TEST_DIR/test_codex.sh"
run_suite "$TEST_DIR/test_qwen.sh"
run_suite "$TEST_DIR/test_opencode.sh"
run_suite "$TEST_DIR/test_hermes.sh"
run_suite "$TEST_DIR/test_harness.sh"
run_suite "$TEST_DIR/test_dynamic_harness.sh"
run_suite "$TEST_DIR/test_pipeline.sh"

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "全部 $SUITES 个测试套件执行完毕"
exit $TOTAL_FAIL
