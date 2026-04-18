#!/usr/bin/env bash
# bridge-ctl.sh — ACP Bridge lifecycle control (systemd-based)
#
# Designed to be called by Kiro agents running INSIDE Bridge for self-bootstrap.
# Uses systemd, so the restart happens outside the Bridge process tree —
# the agent's parent process gets killed, but systemd respawns Bridge cleanly.
#
# Usage:
#   ./bridge-ctl.sh status          — show service status
#   ./bridge-ctl.sh restart         — restart Bridge (safe self-bootstrap)
#   ./bridge-ctl.sh restart-all     — restart LiteLLM + Bridge
#   ./bridge-ctl.sh stop            — stop Bridge
#   ./bridge-ctl.sh logs [N]        — tail last N lines (default 50)
#   ./bridge-ctl.sh logs -f         — follow logs in real time
#   ./bridge-ctl.sh health          — curl /health endpoint
set -euo pipefail

PORT=${ACP_BRIDGE_PORT:-18010}

case "${1:-status}" in
    status)
        systemctl status acp-bridge.service --no-pager -l 2>/dev/null || echo "acp-bridge: not managed by systemd"
        echo "---"
        systemctl status litellm.service --no-pager -l 2>/dev/null || echo "litellm: not managed by systemd"
        ;;
    restart)
        echo "⏳ Restarting ACP Bridge via systemd (this process will be killed)..."
        # Schedule restart in background so the response can be sent before we die
        nohup bash -c 'sleep 2 && sudo systemctl restart acp-bridge.service' >/dev/null 2>&1 &
        echo "✅ Restart scheduled in 2s. Bridge will be back at http://127.0.0.1:$PORT"
        ;;
    restart-all)
        echo "⏳ Restarting LiteLLM + ACP Bridge..."
        nohup bash -c 'sleep 2 && sudo systemctl restart litellm.service && sleep 3 && sudo systemctl restart acp-bridge.service' >/dev/null 2>&1 &
        echo "✅ Restart scheduled. Services will be back shortly."
        ;;
    stop)
        sudo systemctl stop acp-bridge.service
        echo "✅ Bridge stopped"
        ;;
    logs)
        shift
        if [ "${1:-}" = "-f" ]; then
            journalctl -u acp-bridge.service -f
        else
            journalctl -u acp-bridge.service --no-pager -n "${1:-50}"
        fi
        ;;
    health)
        curl -s "http://127.0.0.1:$PORT/health" | python3 -m json.tool
        ;;
    *)
        echo "Usage: $0 {status|restart|restart-all|stop|logs [N]|health}"
        exit 1
        ;;
esac
