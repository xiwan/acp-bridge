#!/bin/bash
# 测试公共库 — 断言函数 + 环境初始化

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CLIENT="$SCRIPT_DIR/skill/acp-client.sh"
TOOLS_CLIENT="$SCRIPT_DIR/tools/tools-client.sh"

export ACP_BRIDGE_URL="${ACP_BRIDGE_URL:-http://127.0.0.1:8002}"
export ACP_RETRIES=1

PASS=0 FAIL=0 SKIP=0

run_test() {
    local name="$1" expect="$2" actual="$3"
    if echo "$actual" | grep -qi "$expect"; then
        echo "✅ $name"
        ((PASS++))
    else
        echo "❌ $name"
        echo "   期望包含: $expect"
        echo "   实际: ${actual:0:200}"
        ((FAIL++))
    fi
}

expect_fail() {
    local name="$1"; shift
    if output=$("$@" 2>&1); then
        echo "❌ $name (应该失败但成功了)"
        echo "   输出: ${output:0:200}"
        ((FAIL++))
    else
        echo "✅ $name (预期失败)"
        ((PASS++))
    fi
}

print_summary() {
    local label="${1:-测试}"
    echo ""
    local msg="=== $label: $PASS 通过, $FAIL 失败"
    [[ $SKIP -gt 0 ]] && msg+=", $SKIP 跳过"
    msg+=" ==="
    echo "$msg"
    return $FAIL
}
