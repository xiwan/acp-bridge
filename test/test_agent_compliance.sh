#!/bin/bash
# ACP Agent 合规测试 — 直接测 agent stdio，不经过 Bridge
#
# 用法:
#   bash test/test_agent_compliance.sh <command> [args...]
#
# 示例:
#   bash test/test_agent_compliance.sh kiro-cli acp --trust-all-tools
#   bash test/test_agent_compliance.sh claude-agent-acp
#   bash test/test_agent_compliance.sh python examples/echo-agent.py
#
# 环境变量:
#   COMPLIANCE_TIMEOUT  — 每个请求超时秒数 (默认 30)
#
# 依赖: bash 4.0+, jq
#
# 退出码:
#   0 — 全部通过
#   N — N 个测试失败

set -uo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/lib.sh"

[[ $# -eq 0 ]] && {
    echo "用法: $0 [--cwd <dir>] <command> [args...]"
    echo "示例: $0 kiro-cli acp --trust-all-tools"
    echo "      $0 --cwd /home/user/projects claude-agent-acp"
    echo "      $0 python3 $SCRIPT_DIR/../examples/echo-agent.py"
    exit 1
}

TIMEOUT="${COMPLIANCE_TIMEOUT:-30}"
SESSION_ID=""
CWD="/tmp"

# --cwd 可选参数
if [[ "${1:-}" == "--cwd" ]]; then
    CWD="$2"
    shift 2
fi

echo "=== ACP Agent 合规测试 ==="
echo "Agent: $*  (cwd: $CWD)"
echo ""

# 启动 agent 作为协程（bash 4.0+）
coproc AGENTPROC { "$@" 2>/dev/null; }
AGENT_PID="${AGENTPROC_PID:-}"

if [[ -z "$AGENT_PID" ]] || ! kill -0 "$AGENT_PID" 2>/dev/null; then
    echo "❌ 启动失败: $*"
    echo "   请确认命令存在且可执行"
    exit 1
fi

cleanup() {
    [[ -n "${AGENT_PID:-}" ]] && { kill "$AGENT_PID" 2>/dev/null || true; wait "$AGENT_PID" 2>/dev/null || true; }
}
trap cleanup EXIT

# 发送 JSON-RPC 消息到 agent stdin
send_rpc() {
    echo "$1" >&"${AGENTPROC[1]}"
}

# 读取所有行直到找到指定 id 的响应，返回全部行内容（含中间通知）
# 返回 0 = 找到响应；返回 1 = timeout
read_until_id() {
    local target_id="$1"
    local timeout="${2:-$TIMEOUT}"
    local all=""
    while IFS= read -t "$timeout" -r line <&"${AGENTPROC[0]}"; do
        [[ -z "$line" ]] && continue
        all+="$line"$'\n'
        local id
        id=$(echo "$line" | jq -r '.id // empty' 2>/dev/null)
        if [[ "$id" == "$target_id" ]]; then
            printf '%s' "$all"
            return 0
        fi
    done
    printf '%s' "$all"
    return 1
}

# 从多行输出中提取指定 jq 路径的值（逐行解析）
extract_field() {
    local json_lines="$1"
    local jq_path="$2"
    while IFS= read -r line; do
        [[ -z "$line" ]] && continue
        local v
        v=$(echo "$line" | jq -r "$jq_path // empty" 2>/dev/null)
        [[ -n "$v" ]] && echo "$v"
    done <<< "$json_lines" | head -1
}

# ─────────────────────────────────────────────
# T1: initialize
# ─────────────────────────────────────────────
echo "--- T1: initialize ---"
send_rpc '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":1,"clientCapabilities":{},"clientInfo":{"name":"compliance-test","version":"0.7.1"}}}'

if resp=$(read_until_id "1" "$TIMEOUT"); then
    run_test "T1.1 返回 result"    '"result"'  "$resp"
    run_test "T1.2 包含 agentInfo" "agentInfo" "$resp"
else
    echo "❌ T1: timeout 或无响应"
    ((FAIL+=2))
fi

# ─────────────────────────────────────────────
# T2: session/new
# ─────────────────────────────────────────────
echo ""
echo "--- T2: session/new ---"
send_rpc "$(jq -cn --arg cwd \"$CWD\" '{"jsonrpc":"2.0","id":2,"method":"session/new","params":{"cwd":$cwd,"mcpServers":[]}}')"

if resp=$(read_until_id "2" "$TIMEOUT"); then
    run_test "T2.1 返回 result"    '"result"'   "$resp"
    run_test "T2.2 包含 sessionId" "sessionId"  "$resp"
    SESSION_ID=$(extract_field "$resp" ".result.sessionId")
    [[ -n "$SESSION_ID" ]] && echo "   session_id: ${SESSION_ID:0:16}…"
else
    echo "❌ T2: timeout 或无响应"
    ((FAIL+=2))
fi

# ─────────────────────────────────────────────
# T3: session/prompt
# ─────────────────────────────────────────────
echo ""
echo "--- T3: session/prompt ---"

if [[ -z "$SESSION_ID" ]]; then
    echo "⚠️  跳过 T3（T2 未获得 sessionId）"
    ((SKIP+=3))
else
    prompt_msg=$(jq -cn --arg sid "$SESSION_ID" \
        '{"jsonrpc":"2.0","id":3,"method":"session/prompt","params":{"sessionId":$sid,"prompt":[{"type":"text","text":"reply with exactly the word: hello"}]}}')
    send_rpc "$prompt_msg"

    if all=$(read_until_id "3" "$TIMEOUT"); then
        run_test "T3.1 有 agent_message_chunk 通知" "agent_message_chunk" "$all"
        run_test "T3.2 有最终 result 响应"          '"result"'            "$all"
        run_test "T3.3 result 包含 stopReason"      "stopReason"          "$all"
    else
        echo "❌ T3: timeout（${TIMEOUT}s 内无完整响应）"
        ((FAIL+=3))
    fi
fi

# ─────────────────────────────────────────────
# T4: ping（可选）
# ─────────────────────────────────────────────
echo ""
echo "--- T4: ping（可选）---"
send_rpc '{"jsonrpc":"2.0","id":4,"method":"ping","params":{}}'

if read_until_id "4" 5 >/dev/null 2>&1; then
    echo "✅ T4 ping: 有响应"
    ((PASS++))
else
    echo "⚠️  T4 ping: 无响应（可选，不影响合规）"
fi

# ─────────────────────────────────────────────
# 汇总
# ─────────────────────────────────────────────
echo ""
print_summary "合规"
