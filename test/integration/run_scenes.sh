#!/bin/bash
# 场景测试统一入口
# 用法: 设置 ACP_TOKEN 后运行 bash test/integration/run_scenes.sh [bridge_url]
set -uo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
export ACP_BRIDGE_URL="${1:-${ACP_BRIDGE_URL:-http://127.0.0.1:18010}}"

TOTAL_PASS=0 TOTAL_FAIL=0 SUITES=0

echo "╔══════════════════════════════════╗"
echo "║   ACP Bridge 场景测试            ║"
echo "║   Bridge: $ACP_BRIDGE_URL"
echo "╚══════════════════════════════════╝"

for script in "$DIR"/scene_*.sh; do
    echo ""
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    bash "$script"
    rc=$?
    ((SUITES++))
    ((TOTAL_FAIL += rc))
done

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "全部 $SUITES 个场景执行完毕 ($TOTAL_FAIL 个失败)"
exit $TOTAL_FAIL
