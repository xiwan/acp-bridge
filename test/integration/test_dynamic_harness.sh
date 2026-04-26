#!/bin/bash
# Dynamic Harness API 测试
set -uo pipefail
source "$(dirname "$0")/../lib.sh"

URL="$ACP_BRIDGE_URL"
AUTH=(-H "Authorization: Bearer ${ACP_TOKEN:-}")

echo "=== Dynamic Harness API 测试 ==="

# --- POST /harness (创建) ---
echo "--- 创建动态 harness ---"
resp=$(curl -s -w "\n%{http_code}" -X POST "$URL/harness" \
  "${AUTH[@]}" -H "Content-Type: application/json" \
  -d '{
    "description": "Test echo harness",
    "profile": {
      "tools": { "shell": { "allowlist": ["echo"] } },
      "orchestration": "free",
      "resources": { "timeout": "60s", "max_turns": 5 },
      "agent": {
        "model": "bedrock/anthropic.claude-sonnet-4-6",
        "system_prompt": "You are a test agent. Be concise.",
        "temperature": 0.1
      }
    }
  }')
code=$(echo "$resp" | tail -1)
body=$(echo "$resp" | sed '$d')
run_test "创建返回 201" "201" "$code"
agent_name=$(echo "$body" | grep -o '"agent_name":"[^"]*"' | cut -d'"' -f4)
run_test "返回 agent_name" "harness-" "$agent_name"
echo "   agent_name: $agent_name"

# --- POST /harness (缺少 profile → 400) ---
echo ""
echo "--- 创建缺少 profile ---"
resp=$(curl -s -w "\n%{http_code}" -X POST "$URL/harness" \
  "${AUTH[@]}" -H "Content-Type: application/json" \
  -d '{"name": "bad"}')
code=$(echo "$resp" | tail -1)
run_test "缺少 profile 返回 400" "400" "$code"

# --- POST /harness (name 冲突 → 409) ---
echo ""
echo "--- 创建 name 冲突 (静态 agent) ---"
resp=$(curl -s -w "\n%{http_code}" -X POST "$URL/harness" \
  "${AUTH[@]}" -H "Content-Type: application/json" \
  -d '{
    "name": "kiro",
    "profile": { "agent": { "model": "test", "system_prompt": "test" } }
  }')
code=$(echo "$resp" | tail -1)
run_test "静态 agent 冲突返回 409" "409" "$code"

# --- GET /harness (列出) ---
echo ""
echo "--- 列出动态 harness ---"
resp=$(curl -s -X GET "$URL/harness" "${AUTH[@]}")
run_test "列出包含新建的 harness" "$agent_name" "$resp"
run_test "返回 pool_usage" "harness_slots" "$resp"

# --- POST /runs (调用动态 harness) ---
echo ""
echo "--- 调用动态 harness ---"
if [[ -n "$agent_name" ]]; then
  resp=$("$CLIENT" -a "$agent_name" "Reply with only the word pong" 2>&1)
  run_test "动态 harness 可调用" "pong\|Pong\|PONG" "$resp"
else
  echo "⚠️  跳过调用测试 (无 agent_name)"
  ((SKIP++))
fi

# --- GET /agents (SDK 注册验证) ---
echo ""
echo "--- SDK agents 列表包含动态 harness ---"
resp=$(curl -s -X GET "$URL/agents" "${AUTH[@]}")
run_test "/agents 包含动态 harness" "$agent_name" "$resp"

# --- DELETE /harness (删除) ---
echo ""
echo "--- 删除动态 harness ---"
if [[ -n "$agent_name" ]]; then
  resp=$(curl -s -w "\n%{http_code}" -X DELETE "$URL/harness/$agent_name" "${AUTH[@]}")
  code=$(echo "$resp" | tail -1)
  run_test "删除返回 200" "200" "$code"

  # 验证已删除
  resp=$(curl -s -X GET "$URL/harness" "${AUTH[@]}")
  if echo "$resp" | grep -q "$agent_name"; then
    echo "❌ 删除后仍在列表中"
    ((FAIL++))
  else
    echo "✅ 删除后不在列表中"
    ((PASS++))
  fi
fi

# --- DELETE /harness (不存在 → 404) ---
echo ""
echo "--- 删除不存在的 harness ---"
resp=$(curl -s -w "\n%{http_code}" -X DELETE "$URL/harness/nonexistent" "${AUTH[@]}")
code=$(echo "$resp" | tail -1)
run_test "不存在返回 404" "404" "$code"

# --- DELETE /harness (静态 agent → 400) ---
echo ""
echo "--- 删除静态 agent ---"
resp=$(curl -s -w "\n%{http_code}" -X DELETE "$URL/harness/kiro" "${AUTH[@]}")
code=$(echo "$resp" | tail -1)
run_test "静态 agent 返回 400" "400" "$code"

print_summary "Dynamic Harness API"
