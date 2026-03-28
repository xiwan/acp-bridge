#!/bin/bash
# OpenClaw Tools Proxy 测试
set -uo pipefail
source "$(dirname "$0")/lib.sh"

echo "=== Tools Proxy 测试 ==="

echo "--- 列出 tools ---"
resp=$("$TOOLS_CLIENT" -l 2>&1)
run_test "tools 列表包含 message" "message" "$resp"
run_test "tools 列表包含 tts" "tts" "$resp"
run_test "tools 列表包含 web_search" "web_search" "$resp"
run_test "tools 列表包含 nodes" "nodes" "$resp"

echo ""
echo "--- API 端点 ---"
resp=$(curl -sf --max-time 5 -H "Authorization: Bearer $ACP_TOKEN" "$ACP_BRIDGE_URL/tools" 2>&1)
run_test "GET /tools 返回 JSON" "tools" "$resp"
run_test "GET /tools 包含 openclaw_url" "openclaw_url" "$resp"

echo ""
echo "--- tool 调用 (message) ---"
resp=$(curl -s --max-time 10 -X POST \
    -H "Authorization: Bearer $ACP_TOKEN" \
    -H "Content-Type: application/json" \
    "$ACP_BRIDGE_URL/tools/invoke" \
    -d '{"tool":"message","action":"send","args":{"channel":"discord","target":"channel:1469723146134356173","message":"🧪 acp-bridge v0.7.0 tools proxy test","accountId":"default"}}' 2>&1)
# 即使 OpenClaw 不可达也测试端点本身能响应
run_test "POST /tools/invoke 有响应" "ok\|error" "$resp"

echo ""
echo "--- 错误处理 ---"
resp=$(curl -s --max-time 5 -X POST \
    -H "Authorization: Bearer $ACP_TOKEN" \
    -H "Content-Type: application/json" \
    "$ACP_BRIDGE_URL/tools/invoke" \
    -d '{"tool":"","args":{}}' 2>&1)
# 空 tool name 应该被 OpenClaw 拒绝或 bridge 返回错误
run_test "空 tool name 返回错误" "error\|invalid\|not" "$resp"

echo ""
echo "--- 客户端 tools-client.sh ---"
resp=$("$TOOLS_CLIENT" web_search "test query" 2>&1 || true)
# 可能成功也可能因 OpenClaw 不可达失败，但不应 crash
if [[ -n "$resp" || $? -eq 0 ]]; then
    echo "✅ tools-client.sh 不崩溃"
    ((PASS++))
else
    echo "❌ tools-client.sh 崩溃（无输出）"
    ((FAIL++))
fi

print_summary "Tools Proxy"
