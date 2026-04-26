#!/bin/bash
# 场景3 — 多 agent 编排：对应 SKILL.md Step 5 + orchestration-patterns.md 模板
# 覆盖: relay(2), dual-view(2), review-write-test(3), race(2), conversation(2)
set -uo pipefail
source "$(dirname "$0")/../lib.sh"

TOKEN="${ACP_TOKEN:-${ACP_BRIDGE_TOKEN:-}}"
BASE="$ACP_BRIDGE_URL"
AUTH="Authorization: Bearer $TOKEN"
TMPF=$(mktemp)
trap 'rm -f "$TMPF"' EXIT

_post() { curl -s -X POST "$BASE$1" -H "$AUTH" -H "Content-Type: application/json" -d "$2"; }
_get()  { curl -s "$BASE$1" -H "$AUTH"; }

_wait() {
    local pid="$1" max="${2:-120}" interval=5
    for _ in $(seq 1 $((max / interval))); do
        local s
        s=$(_get "/pipelines/$pid" | python3 -c "import sys,json; print(json.load(sys.stdin).get('status',''))" 2>/dev/null)
        [[ "$s" == "completed" || "$s" == "failed" ]] && { echo "$s"; return; }
        sleep "$interval"
    done
    echo "timeout"
}

# Submit pipeline, wait, write result to $TMPF. Returns 0 on success.
_submit() {
    local label="$1" payload="$2" max="${3:-120}"
    local resp pid status
    resp=$(_post "/pipelines" "$payload")
    pid=$(echo "$resp" | python3 -c "import sys,json; print(json.load(sys.stdin).get('pipeline_id',''))" 2>/dev/null)
    if [[ -z "$pid" ]]; then
        echo "❌ $label 提交失败: $resp"
        ((FAIL++))
        echo "{}" > "$TMPF"
        return 1
    fi
    echo "✅ $label 提交成功"
    ((PASS++))
    echo "  等待完成 (max ${max}s)..."
    status=$(_wait "$pid" "$max")
    _get "/pipelines/$pid" > "$TMPF"
    if [[ "$status" == "completed" ]]; then
        echo "✅ $label 完成"
        ((PASS++))
    else
        echo "❌ $label 状态: $status"
        ((FAIL++))
    fi
}

_step_result() {
    python3 -c "import sys,json; print(json.load(sys.stdin)['steps'][$1].get('result',''))" < "$TMPF" 2>/dev/null
}

echo "=== 场景: 多 agent 编排 ==="

# ============================================================
# 1. relay 模板 (2 agents): kiro 写文件 → claude 读取验证
# ============================================================
echo ""
echo "--- 1. relay: kiro→claude 文件接力 ---"
_submit "relay" '{
  "mode": "sequence",
  "steps": [
    {"agent": "kiro", "prompt": "在当前目录创建 relay_scene.txt，内容写 relay-payload-42。只创建文件。", "output_as": "s1"},
    {"agent": "claude", "prompt": "读取当前目录 relay_scene.txt，告诉我文件内容。\n上一步：{{s1}}"}
  ]
}' 120
step2=$(_step_result 1)
run_test "relay: claude 读到 relay-payload-42" "relay-payload-42" "$step2"

# ============================================================
# 2. dual-view 模板 (2 agents): 同一问题两个视角
# ============================================================
echo ""
echo "--- 2. dual-view: kiro ‖ claude 并行回答 ---"
_submit "dual-view" '{
  "mode": "parallel",
  "steps": [
    {"agent": "kiro", "prompt": "用一句话解释什么是 ACP 协议"},
    {"agent": "claude", "prompt": "Explain ACP protocol in one sentence"}
  ]
}' 120
s0=$(_step_result 0)
s1=$(_step_result 1)
run_test "dual-view: kiro 有回复" "." "$s0"
run_test "dual-view: claude 有回复" "." "$s1"

# ============================================================
# 3. review-write-test 模板 (3 agents): 写代码→写测试→运行
# ============================================================
echo ""
echo "--- 3. review-write-test: 3-agent 串行 ---"
_submit "review-write-test" '{
  "mode": "sequence",
  "steps": [
    {"agent": "kiro", "prompt": "在当前目录创建 add.py，内容为:\ndef add(a, b):\n    return a + b\n只创建文件，完成后执行 wc -c add.py 确认非空。", "output_as": "impl"},
    {"agent": "claude", "prompt": "读取当前目录 add.py，为它写一个 test_add.py 测试文件（用 assert）。完成后执行 wc -c test_add.py 确认非空。\n上一步：{{impl}}", "output_as": "tests"},
    {"agent": "kiro", "prompt": "在当前目录执行 python3 test_add.py，告诉我测试是否通过。\n上一步：{{tests}}"}
  ]
}' 180
step3=$(_step_result 2)
run_test "review-write-test: 测试执行有结果" "pass\|通过\|ok\|success\|assert\|no error\|ran" "$step3"

# ============================================================
# 4. race 模板 (2 agents): 竞速
# ============================================================
echo ""
echo "--- 4. race: kiro vs claude 竞速 ---"
_submit "race" '{
  "mode": "race",
  "steps": [
    {"agent": "kiro", "prompt": "只回复 kiro-first"},
    {"agent": "claude", "prompt": "Just reply: claude-first"}
  ]
}' 90
winner=$(python3 -c "
import sys,json
d=json.load(sys.stdin)
for s in d.get('steps',[]):
    if s.get('result'):
        print(s['agent'])
        break
" < "$TMPF" 2>/dev/null)
run_test "race: 有赢家" "kiro\|claude" "$winner"

# ============================================================
# 5. conversation 模板 (2 agents): 多轮对话
# ============================================================
echo ""
echo "--- 5. conversation: kiro+claude 讨论 ---"
_submit "conversation" '{
  "mode": "conversation",
  "participants": ["kiro", "claude"],
  "topic": "Python 和 Rust 哪个更适合写 CLI 工具？各说一个优点，然后 STATUS: DONE",
  "config": {"max_turns": 4, "stop_conditions": ["DONE"]}
}' 120
turns=$(python3 -c "import sys,json; print(json.load(sys.stdin).get('turns',0))" < "$TMPF" 2>/dev/null)
run_test "conversation: 有多轮对话 (turns>0)" "." "$([ "${turns:-0}" -gt 0 ] && echo yes || echo no)"

# ============================================================
# 6. 错误场景
# ============================================================
echo ""
echo "--- 6. 编排错误处理 ---"
resp=$(_post "/pipelines" '{"mode": "sequence", "steps": []}')
run_test "空 steps 返回错误" "steps required\|error\|400" "$resp"

resp=$(_post "/pipelines" '{"mode": "conversation", "participants": ["kiro"], "topic": "test"}')
run_test "conversation 少于2人" "at least 2\|error\|400" "$resp"

print_summary "场景: 多 agent 编排"
