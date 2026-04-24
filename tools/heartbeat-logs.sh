#!/bin/bash
# Heartbeat log viewer — show recent agent heartbeat exchanges
# Usage: ./heartbeat-logs.sh [count]       — show last N logs (newest first)
#        ./heartbeat-logs.sh -f [interval]  — tail mode (default 5s)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
source "$SCRIPT_DIR/.env" 2>/dev/null || true

URL="${ACP_BRIDGE_URL:-http://127.0.0.1:18010}"
TOKEN="${ACP_TOKEN:-${ACP_BRIDGE_TOKEN:-}}"

FOLLOW=false
LIMIT=50
INTERVAL=5

if [[ "${1:-}" == "-f" ]]; then
    FOLLOW=true
    INTERVAL="${2:-5}"
else
    LIMIT="${1:-50}"
fi

render() {
    python3 -c "
import sys, json, datetime
d = json.load(sys.stdin)
# API returns newest-first; sort chronologically for round assignment
logs = sorted(d['logs'], key=lambda l: l['ts'])
logs = logs[-$1:]  # keep last N
print(f'Heartbeat logs: {len(logs)}/{d[\"total\"]}')
print()
# Assign rounds chronologically (gap > 10s = new round)
entries = []
round_num = 0
prev_ts = 0
for l in logs:
    if l['ts'] - prev_ts > 10:
        round_num += 1
    prev_ts = l['ts']
    ts = datetime.datetime.fromtimestamp(l['ts']).strftime('%H:%M:%S')
    s = '🔇' if l['silent'] else '💬'
    r = '' if l['silent'] else f' {l[\"response\"][:500]}'
    entries.append((round_num, f'  {ts} | {l[\"agent\"]:10} | {l[\"duration\"]:>5}s | {s}{r}'))
# Display newest first
entries.reverse()
cur_round = None
for rn, line in entries:
    if rn != cur_round:
        if cur_round is not None:
            print()
        print(f'── Round {rn} ──')
        cur_round = rn
    print(line)
"
}

if $FOLLOW; then
    SEEN_TS=0
    ROUND=0
    PREV_TS=0
    while true; do
        OUTPUT=$(curl -s "$URL/heartbeat/logs" -H "Authorization: Bearer $TOKEN" | python3 -c "
import sys, json, datetime
d = json.load(sys.stdin)
logs = sorted(d['logs'], key=lambda l: l['ts'])
logs = [l for l in logs if l['ts'] > $SEEN_TS]
prev_ts = $PREV_TS
round_num = $ROUND
for l in logs:
    if l['ts'] - prev_ts > 10:
        round_num += 1
        if prev_ts > 0:
            print()
        print(f'── Round {round_num} ──')
    prev_ts = l['ts']
    ts = datetime.datetime.fromtimestamp(l['ts']).strftime('%H:%M:%S')
    s = '🔇' if l['silent'] else '💬'
    r = '' if l['silent'] else f' {l[\"response\"][:500]}'
    print(f'  {ts} | {l[\"agent\"]:10} | {l[\"duration\"]:>5}s | {s}{r}')
if logs:
    print(f'__META__={logs[-1][\"ts\"]}|{round_num}|{prev_ts}')
")
        META=$(echo "$OUTPUT" | grep '__META__=' | tail -1 | cut -d= -f2 || true)
        echo "$OUTPUT" | grep -v '__META__=' || true
        if [[ -n "$META" ]]; then
            SEEN_TS=$(echo "$META" | cut -d'|' -f1)
            ROUND=$(echo "$META" | cut -d'|' -f2)
            PREV_TS=$(echo "$META" | cut -d'|' -f3)
        fi
        sleep "$INTERVAL"
    done
else
    curl -s "$URL/heartbeat/logs" -H "Authorization: Bearer $TOKEN" | render "$LIMIT"
fi
