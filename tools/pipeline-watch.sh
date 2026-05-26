#!/bin/bash
# Pipeline watcher — poll a pipeline and print step-status transitions in real time.
# Usage: ./pipeline-watch.sh <pipeline_id> [interval_seconds]
#   interval defaults to 2s.
#
# Exits 0 on completed, 1 on failed, 2 on bad usage / not found.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
# shellcheck disable=SC1091
source "$SCRIPT_DIR/.env" 2>/dev/null || true

URL="${ACP_BRIDGE_URL:-http://127.0.0.1:18010}"
TOKEN="${ACP_TOKEN:-${ACP_BRIDGE_TOKEN:-}}"

if [[ $# -lt 1 ]]; then
    echo "Usage: $0 <pipeline_id> [interval_seconds]" >&2
    echo "  watches a pipeline, prints each step status transition" >&2
    exit 2
fi

PID="$1"
INTERVAL="${2:-2}"

if ! command -v jq >/dev/null 2>&1; then
    echo "ERROR: jq is required but not installed" >&2
    exit 2
fi

AUTH=()
if [[ -n "$TOKEN" ]]; then
    AUTH=(-H "Authorization: Bearer $TOKEN")
fi

LAST=""
START_TS=$(date +%s)

while true; do
    R=$(curl -s --max-time 5 "${AUTH[@]}" "$URL/pipelines/$PID" || true)
    if [[ -z "$R" ]]; then
        echo "[$(date +%H:%M:%S)] ⚠️  no response from bridge, retrying..."
        sleep "$INTERVAL"
        continue
    fi

    # Detect "not found" / error responses
    if echo "$R" | jq -e '.error' >/dev/null 2>&1; then
        echo "[$(date +%H:%M:%S)] ❌ $(echo "$R" | jq -r '.error')"
        exit 2
    fi

    STATUS=$(echo "$R" | jq -r '.status // "unknown"')
    MODE=$(echo "$R" | jq -r '.mode // "?"')
    STEPS_LINE=$(echo "$R" | jq -r '
        [.steps[] |
            (
                if .status == "completed" then "✅"
                elif .status == "failed"  then "❌"
                elif .status == "running" then "🟡"
                else "⏸️ "
                end
            ) + " " + .agent +
            (if (.duration // 0) > 0 then " (\(.duration)s)" else "" end)
        ] | join("  |  ")
    ')

    CUR="$STATUS||$STEPS_LINE"
    if [[ "$CUR" != "$LAST" ]]; then
        ELAPSED=$(( $(date +%s) - START_TS ))
        printf "[%s | +%3ds] %-9s  %s\n" "$(date +%H:%M:%S)" "$ELAPSED" "$STATUS" "$STEPS_LINE"
        LAST="$CUR"
    fi

    case "$STATUS" in
        completed)
            echo
            echo "=== Final result ==="
            echo "$R" | jq '{
                pipeline_id, mode, status, duration, shared_cwd,
                steps: [.steps[] | {agent, status, duration, error,
                    result: (if (.result // "") | length > 240
                             then (.result[0:240] + " ...[truncated]")
                             else .result end)}]
            }'
            exit 0
            ;;
        failed)
            echo
            echo "=== Final result (FAILED) ==="
            echo "$R" | jq '{
                pipeline_id, mode, status, duration, error, shared_cwd,
                steps: [.steps[] | {agent, status, duration, error,
                    result: (if (.result // "") | length > 240
                             then (.result[0:240] + " ...[truncated]")
                             else .result end)}]
            }'
            exit 1
            ;;
    esac

    sleep "$INTERVAL"
done
