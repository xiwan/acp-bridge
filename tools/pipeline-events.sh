#!/bin/bash
# Pipeline event stream — subscribe to SSE lifecycle events.
# Usage: ./pipeline-events.sh <pipeline_id>
#
# Output: pretty-printed event stream, one line per event.
# Streams in real time; closes when pipeline_done is received.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
# shellcheck disable=SC1091
source "$SCRIPT_DIR/.env" 2>/dev/null || true

URL="${ACP_BRIDGE_URL:-http://127.0.0.1:18010}"
TOKEN="${ACP_TOKEN:-${ACP_BRIDGE_TOKEN:-}}"

if [[ $# -lt 1 ]]; then
    echo "Usage: $0 <pipeline_id>" >&2
    echo "  subscribes to /pipelines/<id>/events SSE stream" >&2
    exit 2
fi

PID="$1"

if ! command -v jq >/dev/null 2>&1; then
    echo "ERROR: jq is required" >&2
    exit 2
fi

AUTH=()
if [[ -n "$TOKEN" ]]; then
    AUTH=(-H "Authorization: Bearer $TOKEN")
fi

# -N: no buffering. Pipe through awk to pair "event:" and "data:" lines,
# then jq to pretty-print, then human-readable line per event.
curl -sN --max-time 0 "${AUTH[@]}" "$URL/pipelines/$PID/events" | \
awk '
    BEGIN { evt = ""; data = "" }
    /^event:/ { evt = substr($0, 8); next }
    /^data:/  { data = substr($0, 7) }
    /^$/ {
        if (evt != "" && data != "") {
            print evt "\t" data
            fflush()
        }
        evt = ""; data = ""
    }
' | while IFS=$'\t' read -r EVT DATA; do
    # Prefer event's own emitted timestamp (added in v0.21.1+); fall back to now
    EMITTED=$(echo "$DATA" | jq -r '._emitted_at // empty')
    if [[ -n "$EMITTED" ]]; then
        T=$(date -d "@$EMITTED" +%H:%M:%S 2>/dev/null || date +%H:%M:%S)
    else
        T=$(date +%H:%M:%S)
    fi
    case "$EVT" in
        pipeline_started)
            STEPS=$(echo "$DATA" | jq -r '.steps')
            MODE=$(echo "$DATA" | jq -r '.mode')
            AGENTS=$(echo "$DATA" | jq -r '.agents | join(" → ")')
            printf "[%s] 🚀 pipeline_started  mode=%s steps=%d  %s\n" "$T" "$MODE" "$STEPS" "$AGENTS"
            ;;
        step_started)
            IDX=$(echo "$DATA" | jq -r '.index')
            AGENT=$(echo "$DATA" | jq -r '.agent')
            printf "[%s] ▶️  step_started     idx=%s agent=%s\n" "$T" "$IDX" "$AGENT"
            ;;
        step_progress)
            # Intermediate ACP notification — thinking, tools, message chunks
            IDX=$(echo "$DATA" | jq -r '.index')
            AGENT=$(echo "$DATA" | jq -r '.agent')
            KIND=$(echo "$DATA" | jq -r '.kind')
            case "$KIND" in
                tool.start)
                    TITLE=$(echo "$DATA" | jq -r '.title // "?"')
                    printf "[%s] 🔧 step_progress    idx=%s agent=%s tool.start  %s\n" "$T" "$IDX" "$AGENT" "$TITLE"
                    ;;
                tool.done)
                    TITLE=$(echo "$DATA" | jq -r '.title // "?"')
                    STATUS=$(echo "$DATA" | jq -r '.status // "?"')
                    ICON="✓"; [[ "$STATUS" == "failed" ]] && ICON="✗"
                    printf "[%s] %s step_progress    idx=%s agent=%s tool.done   %s (%s)\n" "$T" "$ICON" "$IDX" "$AGENT" "$TITLE" "$STATUS"
                    ;;
                message.thinking)
                    SNIP=$(echo "$DATA" | jq -r '.content // "" | gsub("\\n"; " ") | .[:60]')
                    printf "[%s] 💭 step_progress    idx=%s agent=%s thinking    %s\n" "$T" "$IDX" "$AGENT" "$SNIP"
                    ;;
                message.part)
                    SNIP=$(echo "$DATA" | jq -r '.content // "" | gsub("\\n"; " ") | .[:60]')
                    printf "[%s] 💬 step_progress    idx=%s agent=%s message     %s\n" "$T" "$IDX" "$AGENT" "$SNIP"
                    ;;
                status)
                    TEXT=$(echo "$DATA" | jq -r '.text // "" | .[:60]')
                    printf "[%s] 📋 step_progress    idx=%s agent=%s plan        %s\n" "$T" "$IDX" "$AGENT" "$TEXT"
                    ;;
                *)
                    printf "[%s] ?  step_progress    idx=%s agent=%s kind=%s\n" "$T" "$IDX" "$AGENT" "$KIND"
                    ;;
            esac
            ;;
        step_completed)
            IDX=$(echo "$DATA" | jq -r '.index')
            AGENT=$(echo "$DATA" | jq -r '.agent')
            DUR=$(echo "$DATA" | jq -r '.duration')
            PREVIEW=$(echo "$DATA" | jq -r '.result_preview // "" | gsub("\\n"; " ") | .[:80]')
            printf "[%s] ✅ step_completed   idx=%s agent=%s dur=%ss  | %s\n" "$T" "$IDX" "$AGENT" "$DUR" "$PREVIEW"
            ;;
        step_failed)
            IDX=$(echo "$DATA" | jq -r '.index')
            AGENT=$(echo "$DATA" | jq -r '.agent')
            DUR=$(echo "$DATA" | jq -r '.duration')
            ERR=$(echo "$DATA" | jq -r '.error // ""')
            printf "[%s] ❌ step_failed      idx=%s agent=%s dur=%ss  | %s\n" "$T" "$IDX" "$AGENT" "$DUR" "$ERR"
            ;;
        pipeline_done)
            STATUS=$(echo "$DATA" | jq -r '.status')
            DUR=$(echo "$DATA" | jq -r '.duration')
            ERR=$(echo "$DATA" | jq -r '.error // ""')
            ICON="✅"; [[ "$STATUS" == "failed" ]] && ICON="❌"
            printf "[%s] %s pipeline_done    status=%s dur=%ss%s\n" "$T" "$ICON" "$STATUS" "$DUR" \
                   "$([[ -n $ERR ]] && echo "  error=$ERR")"
            ;;
        heartbeat)
            # Quiet heartbeat — only print in -v mode (future)
            :
            ;;
        *)
            printf "[%s] ?  %s  %s\n" "$T" "$EVT" "$DATA"
            ;;
    esac
done
