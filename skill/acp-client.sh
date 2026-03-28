#!/bin/bash
# ACP Bridge Client — call remote CLI agents via ACP Bridge HTTP API
# Usage:
#   acp-client.sh <prompt>                    # sync call
#   acp-client.sh --stream <prompt>           # streaming call (SSE)
#   acp-client.sh --card <prompt>             # Markdown card output (for IM display)
#   acp-client.sh -s <uuid> <prompt>          # specify session_id
#   acp-client.sh -a <agent> <prompt>         # specify agent
#   acp-client.sh -l                          # list available agents
#
# Environment variables:
#   ACP_BRIDGE_URL  — Bridge address (default: http://127.0.0.1:18010)
#   ACP_AGENT       — Default agent (default: kiro)
#   ACP_TOKEN       — Auth token (prefer env var over -t flag to avoid ps exposure)
#   ACP_RETRIES     — Retry count on failure (default: 2)
#   ACP_TIMEOUT     — Sync call timeout in seconds (default: 300)
#
# Dependencies: curl, jq, uuidgen

set -euo pipefail

URL="${ACP_BRIDGE_URL:-http://127.0.0.1:18010}"
AGENT="${ACP_AGENT:-kiro}"
TOKEN="${ACP_TOKEN:-}"
SESSION=""
LIST=false
STREAM=false
CARD=false
ASYNC=false
JOB_STATUS=""
CWD=""
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
        --cwd)        CWD="$2"; shift 2 ;;
        --job-status) JOB_STATUS="$2"; shift 2 ;;
        --retries)    MAX_RETRIES="$2"; shift 2 ;;
        -h|--help)    sed -n '2,17s/^# //p' "$0"; exit 0 ;;
        -V|--version) _vf="$(cd "$(dirname "$0")/.." && cat VERSION 2>/dev/null)"; echo "acp-client ${_vf:-unknown}"; exit 0 ;;
        *) break ;;
    esac
done

AUTH=()
[[ -n "$TOKEN" ]] && AUTH=(-H "Authorization: Bearer $TOKEN")

# --- List agents ---
if $LIST; then
    curl -sf --connect-timeout "$CONNECT_TIMEOUT" --max-time 30 "${AUTH[@]}" "$URL/agents" | jq -r '
        (.agents // .) | .[] |
        "  \(.name // .agent | . + " " * (15 - length))  \(.description // "")"'
    exit 0
fi

# --- Query job status ---
if [[ -n "$JOB_STATUS" ]]; then
    resp=$(curl -sf --connect-timeout "$CONNECT_TIMEOUT" --max-time 10 \
        "${AUTH[@]}" "$URL/jobs/$JOB_STATUS") || { echo "❌ Query failed" >&2; exit 1; }
    status=$(echo "$resp" | jq -r '.status // "unknown"')
    case "$status" in
        completed)
            echo "$resp" | jq -r '"**🤖 \(.agent)**\n\n\(.result)\n\n---\n_job: `\(.job_id[:8])…` duration: \(.duration)s_"'
            ;;
        failed)
            echo "$resp" | jq -r '"❌ \(.error)"'
            ;;
        *)
            echo "⏳ Status: $status"
            ;;
    esac
    exit 0
fi

[[ $# -eq 0 ]] && { echo "Usage: acp-client.sh [options] <prompt>" >&2; exit 1; }

PROMPT="$*"

# --- session_id: deterministic UUID v5 ---
[[ -z "$SESSION" ]] && SESSION=$(uuidgen -s -n @dns -N "$AGENT")

# --- Build JSON payload ---
MODE="sync"
$STREAM && MODE="stream"
# card mode forces stream to get structured events
$CARD && MODE="stream"

PAYLOAD=$(jq -n \
    --arg agent "$AGENT" \
    --arg session "$SESSION" \
    --arg prompt "$PROMPT" \
    --arg mode "$MODE" \
    --arg cwd "$CWD" \
    '{agent_name: $agent, session_id: $session,
      input: [{parts: [{content: $prompt, content_type: "text/plain"}]}]}
      + (if $cwd != "" then {cwd: $cwd} else {} end)
      + (if $mode == "stream" then {mode: "stream"} else {} end)')

# --- Async mode: submit and return immediately ---
if $ASYNC; then
    ASYNC_PAYLOAD=$(jq -n \
        --arg agent "$AGENT" \
        --arg session "$SESSION" \
        --arg prompt "$PROMPT" \
        --arg cwd "$CWD" \
        '{agent_name: $agent, session_id: $session, prompt: $prompt}
         + (if $cwd != "" then {cwd: $cwd} else {} end)')
    resp=$(curl -sf --connect-timeout "$CONNECT_TIMEOUT" --max-time 30 \
        -X POST "${AUTH[@]}" "$URL/jobs" \
        -H "Content-Type: application/json" \
        -d "$ASYNC_PAYLOAD") || { echo "❌ Submit failed" >&2; exit 1; }
    job_id=$(echo "$resp" | jq -r '.job_id // empty')
    if [[ -n "$job_id" ]]; then
        echo "✅ Submitted (job: ${job_id:0:8}…)"
        echo "Query status: $0 --job-status $job_id"
        echo "job_id: $job_id" >&2
    else
        echo "❌ $(echo "$resp" | jq -r '.error // "unknown error"')" >&2
        exit 1
    fi
    exit 0
fi

# --- API call with retry ---
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
            echo "❌ Connection failed (retried $MAX_RETRIES times): $URL" >&2
            return 1
        fi
        sleep $((attempt * 2))
    done
}

# --- SSE data line extraction: filter + strip prefix ---
_sse_data_lines() {
    grep --line-buffered '^data: ' | sed -u 's/^data: //'
}

# --- Card mode: single-process jq parses all events ---
if $CARD; then
    TMPDIR_CARD=$(mktemp -d)
    trap 'rm -rf "$TMPDIR_CARD"' EXIT
    : > "$TMPDIR_CARD/thoughts"
    : > "$TMPDIR_CARD/tools"
    : > "$TMPDIR_CARD/content"
    : > "$TMPDIR_CARD/error"

    # single-process jq outputs tab-separated type\tname\tcontent
    retry | _sse_data_lines | \
        jq --unbuffered -r '
            if .type == "message.part" then
                ["part", (.part.name // ""), (.part.content // "")] | @tsv
            elif .type == "run.failed" then
                ["error", "", ((.run.error.code // "error") + ": " + (.run.error.message // "unknown error"))] | @tsv
            else empty end' | \
    while IFS=$'\t' read -t "$IDLE_TIMEOUT" -r etype name content; do
        case "$etype" in
            part)
                if [[ "$name" == "thought" && -n "$content" ]]; then
                    printf '%s' "$content" >> "$TMPDIR_CARD/thoughts"
                elif [[ -n "$content" ]]; then
                    case "$content" in
                        "[tool.start]"*|"[status]"*) ;;
                        "[tool.done]"*)
                            # extract tool name with bash builtins, no fork
                            title="${content#\[tool.done\] }"
                            title="${title%% (*}"
                            echo "✅ \`$title\`" >> "$TMPDIR_CARD/tools"
                            ;;
                        *) printf '%s' "$content" >> "$TMPDIR_CARD/content" ;;
                    esac
                fi
                ;;
            error)
                echo "$content" > "$TMPDIR_CARD/error"
                ;;
        esac
    done

    # Assemble Markdown card
    echo "**🤖 ${AGENT}**"
    echo ""

    if [[ -s "$TMPDIR_CARD/error" ]]; then
        echo "❌ $(cat "$TMPDIR_CARD/error")"
        exit 1
    fi

    if [[ -s "$TMPDIR_CARD/thoughts" ]]; then
        echo "<details>"
        echo "<summary>💭 Thinking</summary>"
        echo ""
        cat "$TMPDIR_CARD/thoughts"
        echo ""
        echo "</details>"
        echo ""
    fi

    if [[ -s "$TMPDIR_CARD/tools" ]]; then
        echo "🔧 **Tools**"
        cat "$TMPDIR_CARD/tools"
        echo ""
    fi

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

# --- Stream mode: single-process jq handles all SSE events ---
if $STREAM; then
    retry | _sse_data_lines | \
        jq --unbuffered -r '
            if .type == "message.part" then
                if .part.name == "thought" then "💭 " + (.part.content // "")
                elif .part.content then .part.content
                else empty end
            elif .type == "run.completed" or .type == "message.completed" then
                if .run.session_id then "session_id: " + .run.session_id | halt_error(0)
                else empty end
            elif .type == "run.failed" then
                "❌ " + (.run.error.code // "error") + ": " + (.run.error.message // "unknown error") | halt_error(1)
            else empty end'
    rc=$?
    (( rc == 1 )) && exit 1
    echo
    exit 0
fi

# --- Sync mode ---
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
        msg=$(echo "$RESP" | jq -r '(.error.code // "error") + ": " + (.error.message // "unknown error")')
        echo "❌ $msg" >&2
        exit 1
        ;;
    *)
        echo "⚠️  Unknown status: $status" >&2
        echo "$RESP"
        ;;
esac
