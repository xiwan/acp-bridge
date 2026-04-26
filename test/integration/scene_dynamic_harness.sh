#!/bin/bash
# 场景5 — 动态 Harness：创建专用 agent、调用、在 pipeline 中使用、清理
# 对应 SKILL.md: "Dynamic Harness" + Preset capability matrix
set -uo pipefail
source "$(dirname "$0")/../lib.sh"

TOKEN="${ACP_TOKEN:-${ACP_BRIDGE_TOKEN:-}}"
BASE="$ACP_BRIDGE_URL"
AUTH="Authorization: Bearer $TOKEN"

_post() { curl -s -X POST "$BASE$1" -H "$AUTH" -H "Content-Type: application/json" -d "$2"; }
_get()  { curl -s "$BASE$1" -H "$AUTH"; }
_del()  { curl -s -X DELETE "$BASE$1" -H "$AUTH"; }

echo "=== 场景: 动态 Harness ==="

CREATED_AGENTS=()

cleanup() {
    for name in "${CREATED_AGENTS[@]}"; do
        _del "/harness/$name" >/dev/null 2>&1
    done
}
trap cleanup EXIT

# --- 1. 创建 reviewer preset ---
echo "--- 1. 创建 reviewer harness ---"
resp=$(_post "/harness" '{
  "profile": "reviewer",
  "description": "Scene test: code reviewer"
}')
reviewer=$(echo "$resp" | jq -r '.agent_name // empty')
run_test "创建 reviewer 返回 agent_name" "harness-" "$reviewer"
[[ -n "$reviewer" ]] && CREATED_AGENTS+=("$reviewer")

# --- 2. 创建 developer preset (指定 model) ---
echo ""
echo "--- 2. 创建 developer harness ---"
resp=$(_post "/harness" '{
  "profile": "developer",
  "description": "Scene test: developer",
  "model": "claude-sonnet"
}')
developer=$(echo "$resp" | jq -r '.agent_name // empty')
run_test "创建 developer 返回 agent_name" "harness-" "$developer"
[[ -n "$developer" ]] && CREATED_AGENTS+=("$developer")

# --- 3. 列出包含新建的 harness ---
echo ""
echo "--- 3. 列出动态 harness ---"
resp=$(_get "/harness")
[[ -n "$reviewer" ]] && run_test "列表包含 reviewer" "$reviewer" "$resp"
[[ -n "$developer" ]] && run_test "列表包含 developer" "$developer" "$resp"

# --- 4. /agents 也包含动态 harness ---
echo ""
echo "--- 4. /agents 注册验证 ---"
resp=$(_get "/agents")
[[ -n "$reviewer" ]] && run_test "/agents 包含 reviewer" "$reviewer" "$resp"

# --- 5. 调用 reviewer (只读，应能回复) ---
echo ""
echo "--- 5. 调用 reviewer ---"
if [[ -n "$reviewer" ]]; then
    resp=$("$CLIENT" -a "$reviewer" "回复 review-ok 就行" 2>&1)
    run_test "reviewer 可调用" "review-ok\|ok\|Review" "$resp"
fi

# --- 6. 在 pipeline 中混用: kiro 写文件 → reviewer 审查 ---
echo ""
echo "--- 6. Pipeline: kiro→reviewer ---"
if [[ -n "$reviewer" ]]; then
    resp=$(_post "/pipelines" "$(cat <<EOF
{
  "mode": "sequence",
  "steps": [
    {"agent": "kiro", "prompt": "在当前目录创建 scene_harness.py，内容为:\ndef greet(name):\n    return f'hello {name}'\n只创建文件。", "output_as": "code"},
    {"agent": "$reviewer", "prompt": "读取当前目录 scene_harness.py 的内容，给出简短代码审查意见。直接输出意见，不要保存文件。\n上一步：{{code}}"}
  ]
}
EOF
)")
    pid=$(echo "$resp" | python3 -c "import sys,json; print(json.load(sys.stdin).get('pipeline_id',''))" 2>/dev/null)
    run_test "pipeline 提交成功" "pipeline_id" "$resp"

    if [[ -n "$pid" ]]; then
        echo "  等待完成 (max 120s)..."
        for _ in $(seq 1 24); do
            s=$(_get "/pipelines/$pid" | python3 -c "import sys,json; print(json.load(sys.stdin).get('status',''))" 2>/dev/null)
            [[ "$s" == "completed" || "$s" == "failed" ]] && break
            sleep 5
        done
        run_test "pipeline 完成" "completed" "$s"
    fi
fi

# --- 7. 错误：重名静态 agent ---
echo ""
echo "--- 7. 错误处理 ---"
resp=$(_post "/harness" '{"name":"kiro","profile":"reviewer"}')
run_test "静态 agent 冲突" "conflict\|409\|exists\|error" "$resp"

# --- 8. 删除 ---
echo ""
echo "--- 8. 清理 ---"
if [[ -n "$reviewer" ]]; then
    resp=$(curl -s -w "\n%{http_code}" -X DELETE "$BASE/harness/$reviewer" -H "$AUTH")
    code=$(echo "$resp" | tail -1)
    run_test "删除 reviewer 返回 200" "200" "$code"
    # 从 cleanup 列表移除已删除的
    CREATED_AGENTS=("${CREATED_AGENTS[@]/$reviewer/}")
fi

print_summary "场景: 动态 Harness"
