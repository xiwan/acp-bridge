#!/usr/bin/env bash
# ACP Bridge — quick start (loads .env, starts LiteLLM + Bridge)
set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"

info()  { printf "\033[1;34m▸\033[0m %s\n" "$*"; }
ok()    { printf "\033[1;32m✔\033[0m %s\n" "$*"; }
warn()  { printf "\033[1;33m⚠\033[0m %s\n" "$*"; }

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
info "Starting ACP Bridge..."
exec uv run main.py --verbose "$@"
