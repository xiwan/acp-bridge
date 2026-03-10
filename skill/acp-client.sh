#!/bin/bash
# ACP Bridge 客户端 — 调用远程 CLI agent（适配 acp-sdk 标准 API）
# 用法:
#   acp-client.sh <prompt>                    # 同步调用
#   acp-client.sh --stream <prompt>           # 流式调用（SSE）
#   acp-client.sh -s <uuid> <prompt>          # 指定 session_id
#   acp-client.sh -a <agent> <prompt>         # 指定 agent
#   acp-client.sh -l                          # 列出可用 agents
#
# 环境变量:
#   ACP_BRIDGE_URL  — Bridge 地址 (默认 http://127.0.0.1:8001)
#   ACP_AGENT       — 默认 agent (默认 kiro)
#   ACP_TOKEN       — 认证 token
#   ACP_RETRIES     — 失败重试次数 (默认 2)
#   ACP_TIMEOUT     — 同步调用超时秒数 (默认 300)
#
# 依赖: curl, jq, uuidgen

set -euo pipefail

URL="${ACP_BRIDGE_URL:-http://127.0.0.1:8001}"
AGENT="${ACP_AGENT:-kiro}"
TOKEN="${ACP_TOKEN:-}"
SESSION=""
LIST=false
STREAM=false
MAX_RETRIES="${ACP_RETRIES:-2}"
CONNECT_TIMEOUT=10
SYNC_TIMEOUT="${ACP_TIMEOUT:-300}"
IDLE_TIMEOUT=120

while [[ $# -gt 0 ]]; do
    case "$1" in
        -u|--url)     URL="$2"; shift 2 ;;
        -a|--agent)   AGENT="$2"; shift 2 ;;
        -s|--session) SESSION="$2"; shift 2 ;;
        -t|--token)   TOKEN="$2"; shift 2 ;;
        -l|--list)    LIST=true; shift ;;
        --stream)     STREAM=true; shift ;;
        --retries)    MAX_RETRIES="$2"; shift 2 ;;
        -h|--help)    sed -n '2,16s/^# //p' "$0"; exit 0 ;;
        *) break ;;
    esac
done

AUTH=()
[[ -n "$TOKEN" ]] && AUTH=(-H "Authorization: Bearer $TOKEN")

# --- 列出 agents ---
if $LIST; then
    curl -sf --connect-timeout "$CONNECT_TIMEOUT" --max-time 30 "${AUTH[@]}" "$URL/agents" | jq -r '
        (.agents // .) | .[] |
        "  \(.name // .agent | . + " " * (15 - length))  \(.description // "")"'
    exit 0
fi

[[ $# -eq 0 ]] && { echo "用法: acp-client.sh [选项] <prompt>" >&2; exit 1; }

PROMPT="$*"

# --- session_id: 确定性 UUID v5 ---
[[ -z "$SESSION" ]] && SESSION=$(uuidgen -s -n @dns -N "$AGENT")

# --- 构建 JSON payload ---
MODE="sync"
$STREAM && MODE="stream"

PAYLOAD=$(jq -n \
    --arg agent "$AGENT" \
    --arg session "$SESSION" \
    --arg prompt "$PROMPT" \
    --arg mode "$MODE" \
    '{agent_name: $agent, session_id: $session,
      input: [{parts: [{content: $prompt, content_type: "text/plain"}]}]}
      + (if $mode == "stream" then {mode: "stream"} else {} end)')

# --- 带重试的调用 ---
call_api() {
    if $STREAM; then
        curl -sN --connect-timeout "$CONNECT_TIMEOUT" \
            -X POST "${AUTH[@]}" "$URL/runs" \
            -H "Content-Type: application/json" \
            -H "Accept: text/event-stream" \
            -d "$PAYLOAD"
    else
        curl -sf --connect-timeout "$CONNECT_TIMEOUT" --max-time "$SYNC_TIMEOUT" \
            -X POST "${AUTH[@]}" "$URL/runs" \
            -H "Content-Type: application/json" \
            -d "$PAYLOAD"
    fi
}

retry() {
    local attempt=0
    while true; do
        if RESP=$(call_api); then
            echo "$RESP"
            return 0
        fi
        ((attempt++))
        if (( attempt > MAX_RETRIES )); then
            echo "❌ 连接失败 (已重试 $MAX_RETRIES 次): $URL" >&2
            return 1
        fi
        sleep $((attempt * 2))
    done
}

# --- 流式模式 ---
if $STREAM; then
    retry | while IFS= read -t "$IDLE_TIMEOUT" -r line; do
        [[ "$line" != data:* ]] && continue
        data="${line#data: }"
        type=$(echo "$data" | jq -r '.type // empty' 2>/dev/null) || continue
        case "$type" in
            message.part)
                content=$(echo "$data" | jq -r '.part.content // empty')
                name=$(echo "$data" | jq -r '.part.name // empty')
                if [[ "$name" == "thought" ]]; then
                    [[ -n "$content" ]] && printf '💭 %s' "$content"
                elif [[ -n "$content" ]]; then
                    printf '%s' "$content"
                fi
                ;;
            run.completed|message.completed)
                sid=$(echo "$data" | jq -r '.run.session_id // empty')
                [[ -n "$sid" ]] && echo "session_id: $sid" >&2
                ;;
            run.failed)
                msg=$(echo "$data" | jq -r '(.run.error.code // "error") + ": " + (.run.error.message // "未知错误")')
                echo "❌ $msg" >&2
                exit 1
                ;;
        esac
    done
    rc=$?
    [[ $rc -gt 128 ]] && echo "❌ 流式读取超时 (${IDLE_TIMEOUT}s 无数据)" >&2
    echo
    exit 0
fi

# --- 同步模式 ---
RESP=$(retry) || exit 1

status=$(echo "$RESP" | jq -r '.status // "unknown"')
case "$status" in
    completed)
        echo "$RESP" | jq -r '
            [.output[]? | .parts[]? |
             select(.name != "thought" and .content != null and .content != "") |
             .content] | join("\n") // "(empty response)"'
        sid=$(echo "$RESP" | jq -r '.session_id // empty')
        [[ -n "$sid" ]] && echo "session_id: $sid" >&2
        ;;
    failed)
        msg=$(echo "$RESP" | jq -r '(.error.code // "error") + ": " + (.error.message // "未知错误")')
        echo "❌ $msg" >&2
        exit 1
        ;;
    *)
        echo "⚠️  未知状态: $status" >&2
        echo "$RESP"
        ;;
esac
