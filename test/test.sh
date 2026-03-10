#!/bin/bash
# ACP Bridge 测试脚本 — 通过 skill/acp-client.sh 客户端测试
# 用法: bash test/test.sh [bridge_url]
# 环境变量: ACP_TOKEN (必填)

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
CLIENT="$SCRIPT_DIR/skill/acp-client.sh"

export ACP_BRIDGE_URL="${1:-http://127.0.0.1:8001}"
export ACP_RETRIES=1

PASS=0 FAIL=0

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
    local name="$1"
    shift
    if output=$("$@" 2>&1); then
        echo "❌ $name (应该失败但成功了)"
        echo "   输出: ${output:0:200}"
        ((FAIL++))
    else
        echo "✅ $name (预期失败)"
        ((PASS++))
    fi
}

echo "=== ACP Bridge 客户端测试 ==="
echo "Bridge: $ACP_BRIDGE_URL"
echo "Client: $CLIENT"
echo ""

# --- 基础接口 ---
echo "--- 基础接口 ---"

resp=$("$CLIENT" -l 2>&1)
run_test "列出 agents 包含 kiro" "kiro" "$resp"

# --- 同步调用 ---
echo ""
echo "--- 同步调用 ---"

resp=$("$CLIENT" -a kiro "回复ok两个字就行" 2>&1)
run_test "同步调用 kiro 有回复" "ok" "$resp"

resp=$("$CLIENT" -a kiro "回复数字42就行" 2>&1)
run_test "同步调用返回内容" "42" "$resp"

# --- 流式调用 ---
echo ""
echo "--- 流式调用 ---"

resp=$("$CLIENT" --stream -a kiro "回复ok两个字就行" 2>&1)
run_test "流式调用有输出" "ok" "$resp"

# --- 多轮对话 (session 复用) ---
echo ""
echo "--- 多轮对话 ---"

SESSION="00000000-0000-0000-0000-000000000099"
resp1=$("$CLIENT" -a kiro -s "$SESSION" "记住暗号是 pineapple，只回复 understood" 2>/dev/null)
run_test "多轮第1轮有回复" "understood\|ok\|记住" "$resp1"

resp2=$("$CLIENT" -a kiro -s "$SESSION" "暗号是什么？只回复暗号本身" 2>/dev/null)
# agent may split across lines, join them
resp2_joined=$(echo "$resp2" | tr -d '\n')
run_test "多轮第2轮记住上下文" "pineapple" "$resp2_joined"

# --- 错误处理 ---
echo ""
echo "--- Claude 同步调用 ---"

resp=$("$CLIENT" -a claude "回复ok两个字就行" 2>/dev/null)
run_test "Claude 同步调用有回复" "ok\|OK" "$resp"

echo ""
echo "--- Claude 流式调用 ---"

resp=$("$CLIENT" --stream -a claude "回复ok两个字就行" 2>/dev/null)
run_test "Claude 流式调用有输出" "ok\|OK" "$resp"

# --- 错误处理 ---
echo ""
echo "--- 异步任务 ---"

resp=$("$CLIENT" --async -a kiro -s "00000000-0000-0000-0000-000000000087" "回复ok两个字就行" 2>/dev/null)
run_test "异步提交返回已提交" "已提交" "$resp"

# 从 stderr 拿 job_id
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

# --- 错误处理 ---
echo ""
echo "--- 错误处理 ---"

expect_fail "不存在的 agent" "$CLIENT" -a nonexistent_agent_xyz "hi"

expect_fail "无效 bridge 地址" env ACP_BRIDGE_URL=http://127.0.0.1:19999 "$CLIENT" "hi"

# --- 汇总 ---
echo ""
echo "=== 结果: $PASS 通过, $FAIL 失败 ==="
exit $FAIL
