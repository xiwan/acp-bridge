#!/bin/bash
# ACP Bridge 测试脚本
# 用法: bash test.sh [host:port]
# 默认: 127.0.0.1:8001

BASE="${1:-http://127.0.0.1:8001}"
TOKEN="${ACP_TOKEN:-}"
AUTH_HEADER=""
[[ -n "$TOKEN" ]] && AUTH_HEADER="Authorization: Bearer $TOKEN"
PASS=0
FAIL=0

run_test() {
    local name="$1" expect="$2" actual="$3"
    if echo "$actual" | grep -q "$expect"; then
        echo "✅ $name"
        ((PASS++))
    else
        echo "❌ $name"
        echo "   期望包含: $expect"
        echo "   实际返回: $actual"
        ((FAIL++))
    fi
}

post_run() {
    local headers=(-H "Content-Type: application/json")
    [[ -n "$AUTH_HEADER" ]] && headers+=(-H "$AUTH_HEADER")
    curl -s -X POST "${headers[@]}" "$BASE/runs" -d "$1"
}

auth_get() {
    local headers=()
    [[ -n "$AUTH_HEADER" ]] && headers+=(-H "$AUTH_HEADER")
    curl -s "${headers[@]}" "$1"
}

echo "=== ACP Bridge 测试 ==="
echo "目标: $BASE"
echo ""

# 1. 列出 agents
echo "--- 基础接口 ---"
resp=$(auth_get "$BASE/agents")
run_test "GET /agents 返回 kiro" '"name":"kiro"' "$resp"

# 2. 正常调用
echo ""
echo "--- 正常调用 ---"
resp=$(post_run '{"agent_name":"kiro","input":[{"role":"user","parts":[{"content":"回复ok就行","content_type":"text/plain"}]}]}')
run_test "正常调用 status=completed" '"status":"completed"' "$resp"
run_test "正常调用有 output" '"content"' "$resp"

# 3. 不存在的 agent
echo ""
echo "--- 错误处理 ---"
resp=$(post_run '{"agent_name":"nonexistent","input":[{"role":"user","parts":[{"content":"hi","content_type":"text/plain"}]}]}')
run_test "不存在的 agent 返回错误" 'error\|not_found\|404\|Agent' "$resp"

# 4. 空 input
resp=$(post_run '{"agent_name":"kiro","input":[]}')
run_test "空 input 不崩溃" 'completed\|error' "$resp"

# 5. 无效 JSON
resp=$(curl -s -X POST "$BASE/runs" -H "Content-Type: application/json" ${AUTH_HEADER:+-H "$AUTH_HEADER"} -d 'not json')
run_test "无效 JSON 返回错误" 'error\|422\|validation' "$resp"

# 6. 中文长文本
echo ""
echo "--- 功能测试 ---"
resp=$(post_run '{"agent_name":"kiro","input":[{"role":"user","parts":[{"content":"用一句话解释什么是ACP协议，只回复那一句话","content_type":"text/plain"}]}]}')
run_test "中文长文本调用成功" '"status":"completed"' "$resp"

# 7. 多 parts
resp=$(post_run '{"agent_name":"kiro","input":[{"role":"user","parts":[{"content":"第一部分：","content_type":"text/plain"},{"content":"回复ok","content_type":"text/plain"}]}]}')
run_test "多 parts 拼接调用成功" '"status":"completed"' "$resp"

# 8. 多条 messages
resp=$(post_run '{"agent_name":"kiro","input":[{"role":"user","parts":[{"content":"记住数字42","content_type":"text/plain"}]},{"role":"user","parts":[{"content":"回复ok","content_type":"text/plain"}]}]}')
run_test "多条 messages 调用成功" '"status":"completed"' "$resp"

# 汇总
echo ""
echo "=== 结果: $PASS 通过, $FAIL 失败 ==="
exit $FAIL
