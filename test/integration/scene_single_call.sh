#!/bin/bash
# 场景1 — 单次调用：快速问答、指定 agent、多种输出模式
# 对应 SKILL.md Step 1: "Single verb, one agent, Q&A, ≤60s → Single call"
set -uo pipefail
source "$(dirname "$0")/../lib.sh"

echo "=== 场景: 单次调用 ==="

# --- 1. 默认 agent 同步调用 ---
echo "--- 1. 默认 agent 同步 ---"
resp=$("$CLIENT" "回复 hello 一个词就行" 2>&1)
run_test "默认 agent 有回复" "hello" "$resp"

# --- 2. 指定 agent 别名 ---
echo ""
echo "--- 2. 指定不同 agent ---"
for agent in kiro claude; do
    resp=$("$CLIENT" -a "$agent" "回复 pong 一个词就行" 2>&1)
    run_test "$agent 同步调用" "pong\|Pong" "$(echo "$resp" | tr -d '\n')"
done

# --- 3. 流式输出 ---
echo ""
echo "--- 3. 流式输出 ---"
resp=$("$CLIENT" --stream -a kiro "回复 stream-ok 就行" 2>&1)
run_test "流式有输出" "stream-ok\|ok" "$resp"

# --- 4. Card 模式（Markdown 卡片） ---
echo ""
echo "--- 4. Card 模式 ---"
resp=$("$CLIENT" --card -a kiro "回复 card-test 就行" 2>&1)
run_test "Card 包含 agent 标题" "🤖.*kiro" "$resp"
run_test "Card 包含 session 信息" "session:" "$resp"

# --- 5. 错误场景：不存在的 agent ---
echo ""
echo "--- 5. 错误处理 ---"
expect_fail "不存在的 agent 返回错误" "$CLIENT" -a fake_agent_xyz "hi"

# --- 6. 列出 agents ---
echo ""
echo "--- 6. 列出可用 agents ---"
resp=$("$CLIENT" -l 2>&1)
run_test "列表包含 kiro" "kiro" "$resp"
run_test "列表包含 claude" "claude" "$resp"

print_summary "场景: 单次调用"
