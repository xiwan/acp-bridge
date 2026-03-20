#!/bin/bash
# OpenClaw Tools 客户端 — 通过 ACP Bridge 调用 OpenClaw tools
# 用法:
#   tools-client.sh -l                                    # 列出可用 tools
#   tools-client.sh <tool> [action] [--arg key=val ...]   # 调用 tool
#   tools-client.sh message send --arg target="channel:xxx" --arg message="hello"
#   tools-client.sh tts "今天构建通过了"
#   tools-client.sh web_search "Python 3.13 new features"
#
# 环境变量:
#   ACP_BRIDGE_URL  — Bridge 地址 (默认 http://127.0.0.1:8002)
#   ACP_TOKEN       — 认证 token
#
# 依赖: curl, jq

set -euo pipefail

URL="${ACP_BRIDGE_URL:-http://127.0.0.1:8002}"
TOKEN="${ACP_TOKEN:-}"
TOOL_NAME=""
TOOL_ACTION=""
LIST=false
declare -A TOOL_ARGS=()
CONNECT_TIMEOUT=10
SYNC_TIMEOUT="${ACP_TIMEOUT:-300}"

while [[ $# -gt 0 ]]; do
    case "$1" in
        -u|--url)   URL="$2"; shift 2 ;;
        -t|--token) TOKEN="$2"; shift 2 ;;
        -l|--list)  LIST=true; shift ;;
        --arg)      IFS='=' read -r _k _v <<< "$2"; TOOL_ARGS["$_k"]="$_v"; shift 2 ;;
        -h|--help)  sed -n '2,11s/^# //p' "$0"; exit 0 ;;
        -V|--version) _vf="$(cd "$(dirname "$0")/.." && cat VERSION 2>/dev/null)"; echo "tools-client ${_vf:-unknown}"; exit 0 ;;
        -*)         echo "未知选项: $1" >&2; exit 1 ;;
        *)
            if [[ -z "$TOOL_NAME" ]]; then
                TOOL_NAME="$1"; shift
            elif [[ -z "$TOOL_ACTION" && ! "$1" =~ [[:space:]] && ${#1} -le 20 && $# -gt 1 ]]; then
                TOOL_ACTION="$1"; shift
            else
                break
            fi ;;
    esac
done

AUTH=()
[[ -n "$TOKEN" ]] && AUTH=(-H "Authorization: Bearer $TOKEN")

# --- 列出 tools ---
if $LIST; then
    curl -sf --connect-timeout "$CONNECT_TIMEOUT" --max-time 30 "${AUTH[@]}" "$URL/tools" | jq -r '
        .tools[] | "  \(.name + " " * (15 - (.name | length)))  \(.description)"'
    exit 0
fi

[[ -z "$TOOL_NAME" ]] && { echo "用法: tools-client.sh <tool> [action] [--arg key=val ...] [text]" >&2; exit 1; }

# --- 构建 args ---
ARGS_JSON="{}"
for _k in "${!TOOL_ARGS[@]}"; do
    ARGS_JSON=$(echo "$ARGS_JSON" | jq --arg k "$_k" --arg v "${TOOL_ARGS[$_k]}" '. + {($k): $v}')
done

# 剩余 positional 参数作为文本 shorthand
if [[ $# -gt 0 ]] && [[ ${#TOOL_ARGS[@]} -eq 0 ]]; then
    _text="$*"
    case "$TOOL_NAME" in
        message)     ARGS_JSON=$(echo "$ARGS_JSON" | jq --arg v "$_text" '. + {message: $v}') ;;
        tts)         ARGS_JSON=$(echo "$ARGS_JSON" | jq --arg v "$_text" '. + {text: $v}') ;;
        web_search)  ARGS_JSON=$(echo "$ARGS_JSON" | jq --arg v "$_text" '. + {query: $v}') ;;
        web_fetch)   ARGS_JSON=$(echo "$ARGS_JSON" | jq --arg v "$_text" '. + {url: $v}') ;;
        *)           ARGS_JSON=$(echo "$ARGS_JSON" | jq --arg v "$_text" '. + {text: $v}') ;;
    esac
fi

PAYLOAD=$(jq -n --arg tool "$TOOL_NAME" --arg action "$TOOL_ACTION" --argjson args "$ARGS_JSON" \
    '{tool: $tool, args: $args} + (if $action != "" then {action: $action} else {} end)')

resp=$(curl -sf --connect-timeout "$CONNECT_TIMEOUT" --max-time "$SYNC_TIMEOUT" \
    -X POST "${AUTH[@]}" "$URL/tools/invoke" \
    -H "Content-Type: application/json" \
    -d "$PAYLOAD") || { echo "❌ tool 调用失败" >&2; exit 1; }

ok=$(echo "$resp" | jq -r '.ok // empty')
if [[ "$ok" == "true" ]]; then
    echo "$resp" | jq -r '.result // "ok"'
else
    echo "❌ $(echo "$resp" | jq -r '.error.message // .error // "未知错误"')" >&2
    exit 1
fi
