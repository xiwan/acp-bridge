#!/bin/bash
# ACP Bridge 客户端 — 调用远程 CLI agent（适配 acp-sdk 标准 API）
# 用法:
#   acp-client.sh <prompt>                    # 同步调用
#   acp-client.sh --stream <prompt>           # 流式调用（SSE）
#   acp-client.sh --card <prompt>             # 输出 Markdown 卡片（适合 IM 展示）
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
CARD=false
ASYNC=false
JOB_STATUS=""
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
        --card)       CARD=true; shift ;;
        --async)      ASYNC=true; shift ;;
        --job-status) JOB_STATUS="$2"; shift 2 ;;
        --retries)    MAX_RETRIES="$2"; shift 2 ;;
        -h|--help)    sed -n '2,17s/^# //p' "$0"; exit 0 ;;
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

# --- 查询 job 状态 ---
if [[ -n "$JOB_STATUS" ]]; then
    resp=$(curl -sf --connect-timeout "$CONNECT_TIMEOUT" --max-time 10 \
        "${AUTH[@]}" "$URL/jobs/$JOB_STATUS") || { echo "❌ 查询失败" >&2; exit 1; }
    status=$(echo "$resp" | jq -r '.status // "unknown"')
    case "$status" in
        completed)
            echo "$resp" | jq -r '"**🤖 \(.agent)**\n\n\(.result)\n\n---\n_job: `\(.job_id[:8])…` duration: \(.duration)s_"'
            ;;
        failed)
            echo "$resp" | jq -r '"❌ \(.error)"'
            ;;
        *)
            echo "⏳ 状态: $status"
            ;;
    esac
    exit 0
fi

[[ $# -eq 0 ]] && { echo "用法: acp-client.sh [选项] <prompt>" >&2; exit 1; }

PROMPT="$*"

# --- session_id: 确定性 UUID v5 ---
[[ -z "$SESSION" ]] && SESSION=$(uuidgen -s -n @dns -N "$AGENT")

# --- 构建 JSON payload ---
MODE="sync"
$STREAM && MODE="stream"
# card 模式强制用 stream 获取结构化事件
$CARD && MODE="stream"

PAYLOAD=$(jq -n \
    --arg agent "$AGENT" \
    --arg session "$SESSION" \
    --arg prompt "$PROMPT" \
    --arg mode "$MODE" \
    '{agent_name: $agent, session_id: $session,
      input: [{parts: [{content: $prompt, content_type: "text/plain"}]}]}
      + (if $mode == "stream" then {mode: "stream"} else {} end)')

# --- Async 模式：提交任务立即返回 ---
if $ASYNC; then
    ASYNC_PAYLOAD=$(jq -n \
        --arg agent "$AGENT" \
        --arg session "$SESSION" \
        --arg prompt "$PROMPT" \
        '{agent_name: $agent, session_id: $session, prompt: $prompt}')
    resp=$(curl -sf --connect-timeout "$CONNECT_TIMEOUT" --max-time 30 \
        -X POST "${AUTH[@]}" "$URL/jobs" \
        -H "Content-Type: application/json" \
        -d "$ASYNC_PAYLOAD") || { echo "❌ 提交失败" >&2; exit 1; }
    job_id=$(echo "$resp" | jq -r '.job_id // empty')
    if [[ -n "$job_id" ]]; then
        echo "✅ 已提交 (job: ${job_id:0:8}…)"
        echo "查询状态: $0 --job-status $job_id"
        echo "job_id: $job_id" >&2
    else
        echo "❌ $(echo "$resp" | jq -r '.error // "未知错误"')" >&2
        exit 1
    fi
    exit 0
fi

# --- 带重试的调用 ---
call_api() {
    if [[ "$MODE" == "stream" ]]; then
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

# --- Card 模式：收集所有事件，最后输出 Markdown 卡片 ---
if $CARD; then
    TMPDIR_CARD=$(mktemp -d)
    trap 'rm -rf "$TMPDIR_CARD"' EXIT
    : > "$TMPDIR_CARD/thoughts"
    : > "$TMPDIR_CARD/tools"
    : > "$TMPDIR_CARD/content"
    ERROR=""

    retry | while IFS= read -t "$IDLE_TIMEOUT" -r line; do
        [[ "$line" != data:* ]] && continue
        data="${line#data: }"
        type=$(echo "$data" | jq -r '.type // empty' 2>/dev/null) || continue
        case "$type" in
            message.part)
                content=$(echo "$data" | jq -r '.part.content // empty')
                name=$(echo "$data" | jq -r '.part.name // empty')
                if [[ "$name" == "thought" && -n "$content" ]]; then
                    printf '%s' "$content" >> "$TMPDIR_CARD/thoughts"
                elif [[ -n "$content" ]]; then
                    # 过滤 tool 事件文本，单独收集
                    if [[ "$content" == "[tool.start]"* ]]; then
                        :  # skip inline tool start
                    elif [[ "$content" == "[tool.done]"* ]]; then
                        # 提取 tool 名称
                        title=$(echo "$content" | sed 's/\[tool\.done\] \(.*\) (.*/\1/')
                        echo "✅ \`$title\`" >> "$TMPDIR_CARD/tools"
                    elif [[ "$content" == "[status]"* ]]; then
                        :  # skip status lines
                    else
                        printf '%s' "$content" >> "$TMPDIR_CARD/content"
                    fi
                fi
                ;;
            run.failed)
                msg=$(echo "$data" | jq -r '(.run.error.code // "error") + ": " + (.run.error.message // "未知错误")')
                echo "$msg" > "$TMPDIR_CARD/error"
                ;;
        esac
    done

    # 组装 Markdown 卡片
    echo "**🤖 ${AGENT}**"
    echo ""

    # 错误
    if [[ -s "$TMPDIR_CARD/error" ]]; then
        echo "❌ $(cat "$TMPDIR_CARD/error")"
        exit 1
    fi

    # Thinking（折叠）
    if [[ -s "$TMPDIR_CARD/thoughts" ]]; then
        echo "<details>"
        echo "<summary>💭 Thinking</summary>"
        echo ""
        cat "$TMPDIR_CARD/thoughts"
        echo ""
        echo "</details>"
        echo ""
    fi

    # Tools（已完成的工具调用）
    if [[ -s "$TMPDIR_CARD/tools" ]]; then
        echo "🔧 **Tools**"
        cat "$TMPDIR_CARD/tools"
        echo ""
    fi

    # 正文
    if [[ -s "$TMPDIR_CARD/content" ]]; then
        cat "$TMPDIR_CARD/content"
    else
        echo "(empty response)"
    fi

    echo ""
    echo "---"
    echo "_session: \`${SESSION:0:8}…\`_"
    exit 0
fi

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
