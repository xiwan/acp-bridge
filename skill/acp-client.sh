#!/bin/bash
# ACP Bridge 客户端 — 调用远程 CLI agent 并格式化输出
# 用法:
#   acp-client.sh <prompt>                    # 同步调用（自动生成 session_id）
#   acp-client.sh --stream <prompt>           # 流式调用（SSE，实时输出）
#   acp-client.sh -s <uuid> <prompt>          # 指定 session_id（必须 UUID 格式）
#   acp-client.sh -a <agent> <prompt>         # 指定 agent
#   acp-client.sh -l                          # 列出可用 agents
#
# 环境变量:
#   ACP_BRIDGE_URL  — Bridge 地址 (默认 http://127.0.0.1:8001)
#   ACP_AGENT       — 默认 agent (默认 kiro)
#   ACP_TOKEN       — 认证 token
#   ACP_RETRIES     — 失败重试次数 (默认 2)

set -euo pipefail

URL="${ACP_BRIDGE_URL:-http://127.0.0.1:8001}"
AGENT="${ACP_AGENT:-kiro}"
TOKEN="${ACP_TOKEN:-}"
SESSION=""
LIST=false
STREAM=false
MAX_RETRIES="${ACP_RETRIES:-2}"

while [[ $# -gt 0 ]]; do
    case "$1" in
        -u|--url)     URL="$2"; shift 2 ;;
        -a|--agent)   AGENT="$2"; shift 2 ;;
        -s|--session) SESSION="$2"; shift 2 ;;
        -t|--token)   TOKEN="$2"; shift 2 ;;
        -l|--list)    LIST=true; shift ;;
        --stream)     STREAM=true; shift ;;
        --retries)    MAX_RETRIES="$2"; shift 2 ;;
        -h|--help)
            sed -n '2,14s/^# //p' "$0"; exit 0 ;;
        *) break ;;
    esac
done

AUTH_HEADER=()
[[ -n "$TOKEN" ]] && AUTH_HEADER=(-H "Authorization: Bearer $TOKEN")

if $LIST; then
    curl -sf "${AUTH_HEADER[@]}" "$URL/agents" | python3 -c "
import sys, json
data = json.load(sys.stdin)
agents = data.get('agents', data) if isinstance(data, dict) else data
for a in agents:
    print(f\"  {a['name']:15s} {a.get('description','')}\")
"
    exit 0
fi

if [[ $# -eq 0 ]]; then
    echo "用法: acp-client.sh [选项] <prompt>" >&2
    echo "  -l            列出可用 agents" >&2
    echo "  -a <agent>    指定 agent (默认: kiro)" >&2
    echo "  -s <uuid>     指定 session_id (UUID 格式)" >&2
    echo "  -u <url>      Bridge 地址" >&2
    echo "  --stream      流式输出 (SSE)" >&2
    echo "  --retries <n> 失败重试次数 (默认: 2)" >&2
    exit 1
fi

PROMPT="$*"

# 未指定 session 时，按 agent 名生成确定性 UUID
if [[ -z "$SESSION" ]]; then
    SESSION=$(python3 -c "import uuid; print(uuid.uuid5(uuid.NAMESPACE_DNS, '$AGENT'))")
fi

# 构建 JSON payload
MODE="sync"
$STREAM && MODE="stream"

PAYLOAD=$(python3 -c "
import json, sys
d = {'agent_name': sys.argv[1], 'session_id': sys.argv[3], 'mode': sys.argv[4],
     'input': [{'role': 'user', 'parts': [{'content': sys.argv[2], 'content_type': 'text/plain'}]}]}
print(json.dumps(d))
" "$AGENT" "$PROMPT" "$SESSION" "$MODE")

# 带重试的调用
call_api() {
    curl -sf -X POST "${AUTH_HEADER[@]}" "$URL/runs" \
        -H "Content-Type: application/json" \
        -d "$PAYLOAD" 2>&1
}

call_api_stream() {
    curl -sN -X POST "${AUTH_HEADER[@]}" "$URL/runs" \
        -H "Content-Type: application/json" \
        -H "Accept: text/event-stream" \
        -d "$PAYLOAD" 2>&1
}

retry() {
    local fn="$1" attempt=0
    while true; do
        if RESP=$($fn); then
            echo "$RESP"
            return 0
        fi
        ((attempt++))
        if (( attempt > MAX_RETRIES )); then
            echo "❌ 连接失败 (已重试 $MAX_RETRIES 次): $URL" >&2
            return 1
        fi
        local wait=$((attempt * 2))
        echo "⚠️  重试 $attempt/$MAX_RETRIES (${wait}s 后)..." >&2
        sleep "$wait"
    done
}

if $STREAM; then
    # 流式模式：解析 SSE 事件，实时输出内容
    retry call_api_stream | python3 -c "
import sys

for line in sys.stdin:
    line = line.rstrip('\n')
    if line.startswith('data: '):
        import json
        try:
            evt = json.loads(line[6:])
        except json.JSONDecodeError:
            continue
        etype = evt.get('type', '')
        if etype == 'message.part':
            part = evt.get('part', {})
            content = part.get('content', '')
            if content:
                print(content, end='', flush=True)
        elif etype == 'run.completed':
            run = evt.get('run', {})
            sid = run.get('session_id')
            if sid:
                print(f'session_id: {sid}', file=sys.stderr)
        elif etype == 'run.failed':
            run = evt.get('run', {})
            err = run.get('error', {})
            print(f\"❌ {err.get('code','error')}: {err.get('message','未知错误')}\", file=sys.stderr)
            sys.exit(1)
print()
" || exit 1
else
    # 同步模式
    RESP=$(retry call_api) || exit 1

    python3 -c "
import sys, json

raw = sys.argv[1]
try:
    r = json.loads(raw)
except json.JSONDecodeError:
    print('❌ 无效响应:', raw, file=sys.stderr)
    sys.exit(1)

status = r.get('status', 'unknown')
if status == 'completed':
    parts = [p['content'] for m in r.get('output', []) for p in m.get('parts', [])]
    print('\n'.join(parts))
    sid = r.get('session_id')
    if sid:
        print(f'session_id: {sid}', file=sys.stderr)
elif status == 'failed':
    err = r.get('error', {})
    print(f\"❌ {err.get('code','error')}: {err.get('message','未知错误')}\", file=sys.stderr)
    sys.exit(1)
else:
    print(f'⚠️  未知状态: {status}', file=sys.stderr)
    print(raw)
" "$RESP"
fi
