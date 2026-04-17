#!/usr/bin/env bash
# ACP Bridge — quick start (loads .env, starts LiteLLM + Bridge)
#
# Usage:
#   ./start.sh              stop old bridge, start new one in background (logs → nohup.out)
#   ./start.sh --foreground start in foreground (original behavior)
#   ./start.sh --stop       stop bridge only
#   ./start.sh --restart    alias for default (stop + start background)
set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"

info()  { printf "\033[1;34m▸\033[0m %s\n" "$*"; }
ok()    { printf "\033[1;32m✔\033[0m %s\n" "$*"; }
warn()  { printf "\033[1;33m⚠\033[0m %s\n" "$*"; }

MODE="background"
case "${1:-}" in
    --foreground|-f) MODE="foreground"; shift ;;
    --stop)          MODE="stop"; shift ;;
    --restart)       MODE="background"; shift ;;
esac

stop_bridge() {
    # Match `main.py` python process (started by `uv run main.py ...`)
    local pids
    pids=$(pgrep -af 'python[0-9.]* main\.py' | awk '{print $1}' || true)
    if [ -z "$pids" ]; then
        info "No running Bridge found"
        return 0
    fi
    info "Stopping Bridge (pids: $(echo $pids | tr '\n' ' '))..."
    kill $pids 2>/dev/null || true
    for _ in $(seq 1 20); do
        pgrep -f 'python[0-9.]* main\.py' >/dev/null || { ok "Bridge stopped"; return 0; }
        sleep 0.5
    done
    warn "Bridge did not exit in 10s; sending SIGKILL"
    pkill -9 -f 'python[0-9.]* main\.py' 2>/dev/null || true
    ok "Bridge killed"
}

if [ "$MODE" = "stop" ]; then
    stop_bridge
    exit 0
fi

# Load .env
if [ -f "$DIR/.env" ]; then
    set -a
    source "$DIR/.env"
    set +a
    ok "Loaded .env"
else
    warn "No .env found — run install.sh first"
fi

# Start LiteLLM if needed and not running
LITELLM_URL="${LITELLM_URL:-http://localhost:4000}"
if grep -q 'LITELLM_API_KEY' "$DIR/.env" 2>/dev/null; then
    if curl -s --max-time 2 "${LITELLM_URL}/health/liveliness" &>/dev/null; then
        ok "LiteLLM already running at $LITELLM_URL"
    else
        info "Starting LiteLLM on port 4000..."
        LITELLM_ARGS="--port 4000"
        [ -f "$DIR/litellm-config.yaml" ] && LITELLM_ARGS="--config $DIR/litellm-config.yaml --port 4000"
        if command -v litellm &>/dev/null; then
            nohup litellm $LITELLM_ARGS > /tmp/litellm.log 2>&1 &
        else
            nohup uvx --python 3.13 --from "litellm[proxy]>=1.83.0" litellm $LITELLM_ARGS > /tmp/litellm.log 2>&1 &
        fi
        LITELLM_PID=$!
        # Wait for LiteLLM to be ready
        for i in $(seq 1 15); do
            if curl -s --max-time 2 "${LITELLM_URL}/health/liveliness" &>/dev/null; then
                ok "LiteLLM started (pid=$LITELLM_PID)"
                break
            fi
            sleep 2
        done
        if ! curl -s --max-time 2 "${LITELLM_URL}/health/liveliness" &>/dev/null; then
            warn "LiteLLM not ready yet — check /tmp/litellm.log"
        fi
    fi
fi

# Start ACP Bridge
stop_bridge
info "Starting ACP Bridge ($MODE)..."
if [ "$MODE" = "foreground" ]; then
    exec uv run main.py --verbose "$@"
else
    nohup uv run main.py --verbose "$@" >> "$DIR/nohup.out" 2>&1 &
    BRIDGE_PID=$!
    disown "$BRIDGE_PID" 2>/dev/null || true
    # Wait for HTTP readiness
    PORT=$(grep -E '^\s*port:' "$DIR/config.yaml" 2>/dev/null | head -1 | awk '{print $2}')
    PORT=${PORT:-18010}
    for i in $(seq 1 20); do
        if curl -s --max-time 1 "http://127.0.0.1:$PORT/health" &>/dev/null; then
            ok "Bridge ready at http://127.0.0.1:$PORT (pid=$BRIDGE_PID, logs: nohup.out)"
            exit 0
        fi
        sleep 1
    done
    warn "Bridge started (pid=$BRIDGE_PID) but /health not responsive yet — tail -f nohup.out"
fi
