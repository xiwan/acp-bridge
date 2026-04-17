#!/bin/bash
# 场景2 — 多轮对话：session 保持上下文、隔离、流式
# 对应 SKILL.md: "/chat → Session Mode" + references/chat.md
set -uo pipefail
source "$(dirname "$0")/../lib.sh"

echo "=== 场景: 多轮对话 ==="

SESSION="00000000-0000-0000-0000-ce0e00020001"

_flat() { tr -d '\n'; }

# --- 1. 第一轮：设置上下文 ---
echo "--- 1. 建立对话上下文 ---"
resp=$("$CLIENT" -a kiro -s "$SESSION" "记住水果是 mango，只回复 ok" 2>/dev/null | _flat)
run_test "第1轮有回复" "ok\|OK\|Ok\|mango\|记" "$resp"

# --- 2. 第二轮：验证上下文保持 ---
echo ""
echo "--- 2. 验证上下文保持 ---"
resp=$("$CLIENT" -a kiro -s "$SESSION" "我说的水果是什么？只回复水果名" 2>/dev/null | _flat)
run_test "第2轮记住上下文" "mango" "$resp"

# --- 3. 第三轮：追加信息 ---
echo ""
echo "--- 3. 追加上下文 ---"
resp=$("$CLIENT" -a kiro -s "$SESSION" "再记住动物是 panda，只回复 ok" 2>/dev/null | _flat)
run_test "第3轮有回复" "ok\|OK\|panda\|记" "$resp"

# --- 4. 第四轮：验证两个信息都在 ---
echo ""
echo "--- 4. 验证多轮累积 ---"
resp=$("$CLIENT" -a kiro -s "$SESSION" "水果和动物分别是什么？用 fruit=xxx animal=xxx 格式回复" 2>/dev/null | _flat)
run_test "记住水果" "mango" "$resp"
run_test "记住动物" "panda" "$resp"

# --- 5. 不同 session 隔离 ---
echo ""
echo "--- 5. Session 隔离 ---"
OTHER_SESSION="00000000-0000-0000-0000-ce0e00020002"
resp=$("$CLIENT" -a kiro -s "$OTHER_SESSION" "我之前说的水果是什么？如果不知道就回复 unknown" 2>/dev/null | _flat)
run_test "不同 session 无上下文" "unknown\|不知道\|没有\|I don\|no fruit\|haven" "$resp"

# --- 6. 流式模式也保持 session ---
echo ""
echo "--- 6. 流式 + session ---"
resp=$("$CLIENT" --stream -a kiro -s "$SESSION" "水果是什么？只回复水果名" 2>&1 | _flat)
run_test "流式模式保持上下文" "mango" "$resp"

print_summary "场景: 多轮对话"
