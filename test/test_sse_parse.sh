#!/bin/bash
# 单元测试 — acp-client.sh 的 SSE 解析逻辑（离线，不需要 Bridge 服务）
# 用法: bash test/test_sse_parse.sh

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
CLIENT="$SCRIPT_DIR/skill/acp-client.sh"

PASS=0 FAIL=0

assert_eq() {
    local name="$1" expected="$2" actual="$3"
    if [[ "$actual" == "$expected" ]]; then
        echo "✅ $name"
        ((PASS++))
    else
        echo "❌ $name"
        echo "   期望: $(echo "$expected" | head -5)"
        echo "   实际: $(echo "$actual" | head -5)"
        ((FAIL++))
    fi
}

assert_contains() {
    local name="$1" pattern="$2" actual="$3"
    if echo "$actual" | grep -qiE "$pattern"; then
        echo "✅ $name"
        ((PASS++))
    else
        echo "❌ $name"
        echo "   期望包含: $pattern"
        echo "   实际: $(echo "$actual" | head -5)"
        ((FAIL++))
    fi
}

# --- 辅助：构造 SSE 数据 ---
sse_part() {
    local content="$1" name="${2:-}"
    local name_field=""
    [[ -n "$name" ]] && name_field=", \"name\": \"$name\""
    echo "data: {\"type\":\"message.part\",\"part\":{\"content\":\"$content\"$name_field}}"
}

sse_completed() {
    local sid="${1:-test-session}"
    echo "data: {\"type\":\"run.completed\",\"run\":{\"session_id\":\"$sid\"}}"
}

sse_failed() {
    local code="$1" msg="$2"
    echo "data: {\"type\":\"run.failed\",\"run\":{\"error\":{\"code\":\"$code\",\"message\":\"$msg\"}}}"
}

# --- 模拟 _sse_data_lines + jq 流式解析 ---
# 提取 acp-client.sh 中的 jq 表达式来测试

echo "=== SSE 解析单元测试 ==="
echo ""

# ---- 流式模式 jq 表达式 ----
STREAM_JQ='
    if .type == "message.part" then
        if .part.name == "thought" then "💭 " + (.part.content // "")
        elif .part.content then .part.content
        else empty end
    elif .type == "run.completed" or .type == "message.completed" then
        if .run.session_id then "session_id: " + .run.session_id | halt_error(0)
        else empty end
    elif .type == "run.failed" then
        "❌ " + (.run.error.code // "error") + ": " + (.run.error.message // "未知错误") | halt_error(1)
    else empty end'

# ---- Card 模式 jq 表达式 ----
CARD_JQ='
    if .type == "message.part" then
        ["part", (.part.name // ""), (.part.content // "")] | @tsv
    elif .type == "run.failed" then
        ["error", "", ((.run.error.code // "error") + ": " + (.run.error.message // "未知错误"))] | @tsv
    else empty end'

echo "--- 流式模式解析 ---"

# 1. 普通文本
out=$(echo '{"type":"message.part","part":{"content":"hello world"}}' | jq -r "$STREAM_JQ")
assert_eq "普通文本输出" "hello world" "$out"

# 2. thought 事件
out=$(echo '{"type":"message.part","part":{"content":"thinking...","name":"thought"}}' | jq -r "$STREAM_JQ")
assert_eq "thought 带前缀" "💭 thinking..." "$out"

# 3. 空 content 不输出
out=$(echo '{"type":"message.part","part":{"content":""}}' | jq -r "$STREAM_JQ")
assert_eq "空 content 无输出" "" "$out"

# 4. 无 content 字段不输出
out=$(echo '{"type":"message.part","part":{"name":"thought"}}' | jq -r "$STREAM_JQ")
assert_eq "无 content thought 输出空" "💭 " "$out"

# 5. run.completed 输出 session_id
out=$(echo '{"type":"run.completed","run":{"session_id":"abc-123"}}' | jq -r "$STREAM_JQ" 2>&1)
assert_eq "completed 输出 session_id" "session_id: abc-123" "$out"

# 6. run.failed 输出错误
out=$(echo '{"type":"run.failed","run":{"error":{"code":"timeout","message":"too slow"}}}' | jq -r "$STREAM_JQ" 2>&1)
assert_eq "failed 输出错误" "❌ timeout: too slow" "$out"

# 7. 多事件流
out=$(printf '%s\n%s\n%s\n' \
    '{"type":"message.part","part":{"content":"line1"}}' \
    '{"type":"message.part","part":{"content":"line2"}}' \
    '{"type":"message.part","part":{"content":"line3"}}' | jq -r "$STREAM_JQ")
assert_eq "多事件连续输出" "$(printf 'line1\nline2\nline3')" "$out"

# 8. 未知事件类型被忽略
out=$(echo '{"type":"unknown.event","data":"ignored"}' | jq -r "$STREAM_JQ")
assert_eq "未知事件被忽略" "" "$out"

echo ""
echo "--- Card 模式解析 ---"

# 9. card: 普通文本
out=$(echo '{"type":"message.part","part":{"content":"hello"}}' | jq -r "$CARD_JQ")
assert_eq "card 普通文本" "$(printf 'part\t\thello')" "$out"

# 10. card: thought
out=$(echo '{"type":"message.part","part":{"content":"deep thought","name":"thought"}}' | jq -r "$CARD_JQ")
assert_eq "card thought" "$(printf 'part\tthought\tdeep thought')" "$out"

# 11. card: error
out=$(echo '{"type":"run.failed","run":{"error":{"code":"err","message":"boom"}}}' | jq -r "$CARD_JQ")
assert_eq "card error" "$(printf 'error\t\terr: boom')" "$out"

# 12. card: tool.done 事件 bash 提取
content="[tool.done] read_file (0.5s)"
title="${content#\[tool.done\] }"
title="${title%% (*}"
assert_eq "card tool 名称提取" "read_file" "$title"

# 13. card: tool.start 被跳过（bash case 匹配）
content="[tool.start] search"
case "$content" in
    "[tool.start]"*) result="skipped" ;;
    *) result="not_skipped" ;;
esac
assert_eq "card tool.start 跳过" "skipped" "$result"

echo ""
echo "--- SSE 行过滤 ---"

# 14. _sse_data_lines 过滤
input=$(printf 'event: message\ndata: {"type":"message.part","part":{"content":"ok"}}\n\nretry: 3000\ndata: {"type":"run.completed","run":{}}\n')
out=$(echo "$input" | grep '^data: ' | sed 's/^data: //')
expected=$(printf '{"type":"message.part","part":{"content":"ok"}}\n{"type":"run.completed","run":{}}')
assert_eq "SSE 行过滤只保留 data:" "$expected" "$out"

echo ""
echo "--- 端到端：模拟 SSE → 流式输出 ---"

# 15. 完整 SSE 流 → 流式 jq 处理 (halt_error 输出到 stderr，分开验证)
sse_stream=$(printf '%s\n%s\n%s\n' \
    "data: {\"type\":\"message.part\",\"part\":{\"content\":\"Hello \",\"name\":\"thought\"}}" \
    "data: {\"type\":\"message.part\",\"part\":{\"content\":\"World\"}}" \
    "data: {\"type\":\"run.completed\",\"run\":{\"session_id\":\"s1\"}}")
stdout_out=$(echo "$sse_stream" | grep '^data: ' | sed 's/^data: //' | jq -r "$STREAM_JQ" 2>/dev/null)
stderr_out=$(echo "$sse_stream" | grep '^data: ' | sed 's/^data: //' | jq -r "$STREAM_JQ" 2>&1 1>/dev/null)
assert_eq "端到端流式 stdout" "$(printf '💭 Hello \nWorld')" "$stdout_out"
assert_contains "端到端流式 stderr 含 session_id" "session_id: s1" "$stderr_out"

# 16. 完整 SSE 流 → card jq 处理
out=$(echo "$sse_stream" | grep '^data: ' | sed 's/^data: //' | jq -r "$CARD_JQ")
line1=$(echo "$out" | head -1)
line2=$(echo "$out" | tail -1)
assert_eq "端到端 card thought" "$(printf 'part\tthought\tHello ')" "$line1"
assert_eq "端到端 card content" "$(printf 'part\t\tWorld')" "$line2"

# --- 汇总 ---
echo ""
echo "=== 结果: $PASS 通过, $FAIL 失败 ==="
exit $FAIL
