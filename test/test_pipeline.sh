#!/bin/bash
# Pipeline 集成测试 — 全模式 + 全 agent 覆盖 + shared_cwd 验证
set -uo pipefail
source "$(dirname "$0")/lib.sh"

TOKEN="${ACP_TOKEN:-${ACP_BRIDGE_TOKEN:-}}"
BASE="${ACP_BRIDGE_URL:-http://127.0.0.1:18010}"
AUTH="Authorization: Bearer $TOKEN"

_post() { curl -s -X POST "$BASE$1" -H "$AUTH" -H "Content-Type: application/json" -d "$2"; }
_get()  { curl -s "$BASE$1" -H "$AUTH"; }

_wait_pipeline() {
    local pid="$1" max="${2:-60}" interval="${3:-5}"
    for i in $(seq 1 $((max / interval))); do
        status=$(_get "/pipelines/$pid" | python3 -c "import sys,json; print(json.load(sys.stdin).get('status',''))" 2>/dev/null)
        if [[ "$status" == "completed" || "$status" == "failed" ]]; then
            echo "$status"
            return
        fi
        sleep "$interval"
    done
    echo "timeout"
}

_submit_and_wait() {
    local label="$1" payload="$2" max="${3:-120}"
    resp=$(_post "/pipelines" "$payload")
    pid=$(echo "$resp" | python3 -c "import sys,json; print(json.load(sys.stdin).get('pipeline_id',''))" 2>/dev/null)
    run_test "$label 提交成功" "pipeline_id" "$resp"
    if [[ -z "$pid" ]]; then return 1; fi
    echo "  等待完成 (max ${max}s)..."
    status=$(_wait_pipeline "$pid" "$max")
    result=$(_get "/pipelines/$pid")
    run_test "$label 状态 completed" "completed" "$status"
    echo "$result"
}

echo "=== Pipeline 测试 ==="

# ============================================================
# 1. Sequence: kiro→claude 共享目录文件传递
# ============================================================
echo ""
echo "--- 1. Sequence: kiro→claude 文件传递 ---"
result=$(_submit_and_wait "seq:kiro→claude" '{
  "mode": "sequence",
  "steps": [
    {"agent": "kiro", "prompt": "在当前工作目录创建 seq_test.txt，内容写 hello-from-kiro。只创建文件，不要多余输出。", "output_as": "s1"},
    {"agent": "claude", "prompt": "读取当前目录的 seq_test.txt，告诉我文件内容是什么。\n上一步：{{s1}}"}
  ]
}' 120)
if [[ -n "$result" ]]; then
    run_test "seq:kiro→claude shared_cwd" "sequence/" "$result"
    step2=$(echo "$result" | python3 -c "import sys,json; print(json.load(sys.stdin)['steps'][1].get('result',''))" 2>/dev/null)
    run_test "seq:claude 读到 hello-from-kiro" "hello-from-kiro" "$step2"
fi

# ============================================================
# 2. Sequence: kiro→codex (PTY) 跨模式文件传递
# ============================================================
echo ""
echo "--- 2. Sequence: kiro→codex 跨模式文件传递 ---"
result=$(_submit_and_wait "seq:kiro→codex" '{
  "mode": "sequence",
  "steps": [
    {"agent": "kiro", "prompt": "在当前工作目录创建 for_codex.txt，内容写 hello-from-kiro-to-codex。只创建文件。", "output_as": "s1"},
    {"agent": "codex", "prompt": "Read for_codex.txt in current directory and tell me its content.\nPrevious step: {{s1}}"}
  ]
}' 180)
if [[ -n "$result" ]]; then
    step2=$(echo "$result" | python3 -c "import sys,json; print(json.load(sys.stdin)['steps'][1].get('result',''))" 2>/dev/null)
    run_test "seq:codex 读到 hello-from-kiro-to-codex" "hello-from-kiro-to-codex" "$step2"
fi

# ============================================================
# 3. Sequence: kiro→qwen 文件传递
# ============================================================
echo ""
echo "--- 3. Sequence: kiro→qwen 文件传递 ---"
result=$(_submit_and_wait "seq:kiro→qwen" '{
  "mode": "sequence",
  "steps": [
    {"agent": "kiro", "prompt": "在当前工作目录创建 relay.txt，内容写 step1-kiro。只创建文件。", "output_as": "s1"},
    {"agent": "qwen", "prompt": "读取当前目录 relay.txt，告诉我文件内容。\n上一步：{{s1}}"}
  ]
}' 180)
if [[ -n "$result" ]]; then
    step2=$(echo "$result" | python3 -c "import sys,json; print(json.load(sys.stdin)['steps'][1].get('result',''))" 2>/dev/null)
    run_test "seq:qwen 读到 step1-kiro" "step1-kiro" "$step2"
fi

# ============================================================
# 4. Sequence: kiro→opencode 文件传递
# ============================================================
echo ""
echo "--- 4. Sequence: kiro→opencode 文件传递 ---"
result=$(_submit_and_wait "seq:kiro→opencode" '{
  "mode": "sequence",
  "steps": [
    {"agent": "kiro", "prompt": "在当前工作目录创建 for_oc.txt，内容写 hello-from-kiro-to-opencode。只创建文件。", "output_as": "s1"},
    {"agent": "opencode", "prompt": "Read for_oc.txt in current directory and tell me its content.\nPrevious: {{s1}}"}
  ]
}' 180)
if [[ -n "$result" ]]; then
    step2=$(echo "$result" | python3 -c "import sys,json; print(json.load(sys.stdin)['steps'][1].get('result',''))" 2>/dev/null)
    run_test "seq:opencode 读到 hello-from-kiro-to-opencode" "hello-from-kiro-to-opencode" "$step2"
fi

# ============================================================
# 5. Parallel: 5 agent 同时写文件到共享目录
# ============================================================
echo ""
echo "--- 4. Parallel: 5 agent 同时写文件 ---"
result=$(_submit_and_wait "parallel:5-agent" '{
  "mode": "parallel",
  "steps": [
    {"agent": "kiro", "prompt": "在当前工作目录创建 p_kiro.txt，内容写 kiro-was-here。只创建文件。"},
    {"agent": "claude", "prompt": "Create p_claude.txt in current directory with content claude-was-here. Just create the file."},
    {"agent": "codex", "prompt": "Create p_codex.txt in current directory with content codex-was-here. Just create the file."},
    {"agent": "qwen", "prompt": "在当前工作目录创建 p_qwen.txt，内容写 qwen-was-here。只创建文件。"},
    {"agent": "opencode", "prompt": "Create p_opencode.txt in current directory with content opencode-was-here. Just create the file."}
  ]
}' 180)
if [[ -n "$result" ]]; then
    cwd=$(echo "$result" | python3 -c "import sys,json; print(json.load(sys.stdin).get('shared_cwd',''))" 2>/dev/null)
    run_test "parallel:5-agent cwd 包含 parallel/" "parallel/" "$cwd"
    if [[ -n "$cwd" && -d "$cwd" ]]; then
        file_count=$(ls "$cwd"/p_*.txt 2>/dev/null | wc -l)
        echo "  共享目录文件: $(ls "$cwd"/p_*.txt 2>/dev/null | xargs -I{} basename {})"
        run_test "parallel:至少3个agent写了文件" "1" "$([ "$file_count" -ge 3 ] && echo 1 || echo 0)"
    fi
fi

# ============================================================
# 6. Race: kiro vs claude vs qwen
# ============================================================
echo ""
echo "--- 6. Race: kiro vs claude vs qwen ---"
result=$(_submit_and_wait "race:3-agent" '{
  "mode": "race",
  "steps": [
    {"agent": "kiro", "prompt": "只回复 kiro-wins"},
    {"agent": "claude", "prompt": "Just reply: claude-wins"},
    {"agent": "qwen", "prompt": "只回复 qwen-wins"}
  ]
}' 90)
if [[ -n "$result" ]]; then
    run_test "race:3-agent shared_cwd" "race/" "$result"
fi

# ============================================================
# 7. Random: 从 5 个 agent 随机选一个
# ============================================================
echo ""
echo "--- 7. Random: 5 agent 随机选 ---"
result=$(_submit_and_wait "random:5-agent" '{
  "mode": "random",
  "steps": [
    {"agent": "kiro", "prompt": "只回复 picked-kiro"},
    {"agent": "claude", "prompt": "Just reply: picked-claude"},
    {"agent": "codex", "prompt": "Just reply: picked-codex"},
    {"agent": "qwen", "prompt": "只回复 picked-qwen"},
    {"agent": "opencode", "prompt": "Just reply: picked-opencode"}
  ]
}' 180)
if [[ -n "$result" ]]; then
    run_test "random:5-agent shared_cwd" "random/" "$result"
fi

# ============================================================
# 8. Conversation: kiro + claude
# ============================================================
echo ""
echo "--- 8. Conversation: kiro+claude ---"
result=$(_submit_and_wait "conv:kiro+claude" '{
  "mode": "conversation",
  "participants": ["kiro", "claude"],
  "topic": "在共享目录创建 conv_test.txt 写入 hello，然后 STATUS: DONE",
  "config": {"max_turns": 3, "stop_conditions": ["DONE"]}
}' 120)
if [[ -n "$result" ]]; then
    run_test "conv:kiro+claude cwd" "conversation/" "$result"
fi

# ============================================================
# 9. 列表查询
# ============================================================
echo ""
echo "--- 9. 列表查询 ---"
resp=$(_get "/pipelines")
run_test "列表返回 pipelines" "pipelines" "$resp"

# ============================================================
# 10. 错误处理
# ============================================================
echo ""
echo "--- 10. 错误处理 ---"
resp=$(_post "/pipelines" '{"mode": "invalid", "steps": [{"agent": "kiro", "prompt": "hi"}]}')
run_test "无效 mode 返回 400" "invalid mode" "$resp"

resp=$(_post "/pipelines" '{"mode": "sequence", "steps": []}')
run_test "空 steps 返回 400" "steps required" "$resp"

resp=$(_get "/pipelines/nonexistent-id")
run_test "不存在的 pipeline 返回 404" "not found" "$resp"

resp=$(_post "/pipelines" '{"mode": "conversation", "participants": ["kiro"], "topic": "test"}')
run_test "conversation 少于2人返回 400" "at least 2" "$resp"

resp=$(_post "/pipelines" '{"mode": "conversation", "participants": ["kiro", "kiro"], "topic": ""}')
run_test "conversation 无 topic 返回 400" "requires a topic" "$resp"

print_summary "Pipeline"
