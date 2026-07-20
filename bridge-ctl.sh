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
#   ./bridge-ctl.sh live            — check process liveness
#   ./bridge-ctl.sh ready           — check request readiness
#   ./bridge-ctl.sh health          — curl /health endpoint
#   ./bridge-ctl.sh orphans         — show manually-started Bridge processes
set -euo pipefail

BRIDGE_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
SERVICE=acp-bridge.service
LITELLM_SERVICE=litellm.service
PORT=${ACP_BRIDGE_PORT:-18010}
SYSTEMD_RUN=$(command -v systemd-run)
SYSTEMCTL=$(command -v systemctl)

schedule_service_action() {
    local purpose=$1
    shift
    local unit="acp-bridge-${purpose}-$(date +%s)"
    sudo "$SYSTEMD_RUN" --quiet --collect --unit="$unit" --on-active=2s "$@"
}

service_cgroup() {
    systemctl show -p ControlGroup --value "$SERVICE" 2>/dev/null || true
}

pid_in_service_cgroup() {
    local pid=$1
    local cgroup=$2
    [ -n "$cgroup" ] || return 1
    [ -r "/proc/$pid/cgroup" ] || return 1
    grep -qF "$cgroup" "/proc/$pid/cgroup"
}

list_orphan_bridge_processes() {
    local cgroup
    local uv_re
    local py_re
    cgroup=$(service_cgroup)
    uv_re='(^|[[:space:]])uv[[:space:]]+run([[:space:]][^[:space:]]+)*[[:space:]]+([^[:space:]]*/)?main\.py([[:space:]]|$)'
    py_re='(^|[[:space:]])python[0-9.]*[[:space:]]+([^[:space:]]*/)?main\.py([[:space:]]|$)'

    ps -eo pid=,ppid=,comm=,args= | while read -r pid ppid comm args; do
        [ -n "${pid:-}" ] || continue
        [ "$pid" != "$$" ] || continue
        case "$comm" in
            ps|awk|grep|sed|bash|sh|zsh|fish|timeout)
                continue
                ;;
        esac
        if [[ "$args" != *"$BRIDGE_DIR/main.py"* && ! "$args" =~ $uv_re && ! "$args" =~ $py_re ]]; then
            continue
        fi

        local cwd
        cwd=$(readlink "/proc/$pid/cwd" 2>/dev/null || true)
        if [ "$cwd" != "$BRIDGE_DIR" ] && [[ "$args" != *"$BRIDGE_DIR/main.py"* ]]; then
            continue
        fi
        if pid_in_service_cgroup "$pid" "$cgroup"; then
            continue
        fi

        printf '  pid=%s ppid=%s cwd=%s cmd=%s\n' "$pid" "$ppid" "${cwd:-?}" "$args"
    done
}

warn_orphan_bridge_processes() {
    local orphans
    orphans=$(list_orphan_bridge_processes)
    if [ -z "$orphans" ]; then
        echo "No orphan Bridge processes detected."
        return 0
    fi

    echo "⚠️  Orphan Bridge process(es) detected outside $SERVICE:"
    printf '%s\n' "$orphans"
    echo "These can keep port $PORT busy and make systemd restarts ineffective."
    return 1
}

case "${1:-status}" in
    status)
        systemctl status "$SERVICE" --no-pager -l 2>/dev/null || echo "acp-bridge: not managed by systemd"
        echo "---"
        systemctl status "$LITELLM_SERVICE" --no-pager -l 2>/dev/null || echo "litellm: not managed by systemd"
        echo "---"
        warn_orphan_bridge_processes || true
        ;;
    restart)
        warn_orphan_bridge_processes || true
        echo "⏳ Restarting ACP Bridge via systemd (this process will be killed)..."
        # A transient systemd timer survives when Bridge kills the calling agent process.
        schedule_service_action restart "$SYSTEMCTL" restart "$SERVICE"
        echo "✅ Restart scheduled in 2s. Bridge will be back at http://127.0.0.1:$PORT"
        ;;
    restart-all)
        warn_orphan_bridge_processes || true
        echo "⏳ Restarting LiteLLM + ACP Bridge..."
        schedule_service_action restart-all /bin/bash -c \
            "'$SYSTEMCTL' restart '$LITELLM_SERVICE' && sleep 3 && '$SYSTEMCTL' restart '$SERVICE'"
        echo "✅ Restart scheduled. Services will be back shortly."
        ;;
    stop)
        sudo systemctl stop "$SERVICE"
        echo "✅ Bridge stopped"
        ;;
    logs)
        shift
        if [ "${1:-}" = "-f" ]; then
            journalctl -u "$SERVICE" -f
        else
            journalctl -u "$SERVICE" --no-pager -n "${1:-50}"
        fi
        ;;
    health)
        curl -s "http://127.0.0.1:$PORT/health" | python3 -m json.tool
        ;;
    live|ready)
        curl -s "http://127.0.0.1:$PORT/$1" | python3 -m json.tool
        ;;
    orphans)
        warn_orphan_bridge_processes
        ;;
    *)
        echo "Usage: $0 {status|restart|restart-all|stop|logs [N]|live|ready|health|orphans}"
        exit 1
        ;;
esac
