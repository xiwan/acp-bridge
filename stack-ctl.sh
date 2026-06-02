#!/usr/bin/env bash
# stack-ctl.sh — Manage the full service stack (mantle-proxy → litellm → acp-bridge)
set -euo pipefail

SERVICES=(mantle-proxy litellm acp-bridge)

usage() {
  cat <<EOF
Usage: $0 <command>

Commands:
  start       Start all services (dependency order)
  stop        Stop all services (reverse order)
  restart     Restart all services (reverse stop, then start)
  status      Show status of all services
  logs [N]    Show last N lines of each service (default: 30)
  health      Check health endpoints
EOF
  exit 1
}

start_all() {
  echo "▶ Starting stack: ${SERVICES[*]}"
  for svc in "${SERVICES[@]}"; do
    sudo systemctl start "$svc"
    echo "  ✅ $svc started"
    sleep 1
  done
  echo "⏳ Waiting for services to stabilize..."
  sleep 3
  health_check
}

stop_all() {
  echo "⏹ Stopping stack (reverse order)..."
  for ((i=${#SERVICES[@]}-1; i>=0; i--)); do
    svc="${SERVICES[$i]}"
    sudo systemctl stop "$svc" 2>/dev/null && echo "  ⏹ $svc stopped" || echo "  ⚠ $svc was not running"
  done
}

restart_all() {
  echo "🔄 Restarting full stack..."
  stop_all
  sleep 2
  start_all
}

status_all() {
  for svc in "${SERVICES[@]}"; do
    state=$(systemctl is-active "$svc" 2>/dev/null || echo "unknown")
    case "$state" in
      active)   icon="✅" ;;
      inactive) icon="⏹" ;;
      failed)   icon="❌" ;;
      *)        icon="❓" ;;
    esac
    pid=$(systemctl show -p MainPID "$svc" 2>/dev/null | cut -d= -f2)
    mem=$(systemctl show -p MemoryCurrent "$svc" 2>/dev/null | cut -d= -f2)
    if [ "$mem" != "" ] && [ "$mem" != "[not set]" ] && [ "$mem" -gt 0 ] 2>/dev/null; then
      mem_mb=$((mem / 1024 / 1024))M
    else
      mem_mb="-"
    fi
    printf "  %s %-15s state=%-8s pid=%-7s mem=%s\n" "$icon" "$svc" "$state" "$pid" "$mem_mb"
  done
}

logs_all() {
  local n="${1:-30}"
  for svc in "${SERVICES[@]}"; do
    echo "═══ $svc (last $n lines) ═══"
    journalctl -u "$svc" --no-pager -n "$n" 2>/dev/null | tail -"$n"
    echo ""
  done
}

health_check() {
  echo "🏥 Health check:"
  # mantle-proxy
  if curl -sf -o /dev/null -w "" http://127.0.0.1:4010/ 2>/dev/null || curl -sf -o /dev/null http://127.0.0.1:4010/ 2>/dev/null; then
    echo "  ✅ mantle-proxy :4010 reachable"
  else
    code=$(curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:4010/ 2>/dev/null || echo "000")
    [ "$code" != "000" ] && echo "  ✅ mantle-proxy :4010 reachable (HTTP $code)" || echo "  ❌ mantle-proxy :4010 unreachable"
  fi
  # litellm
  code=$(curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:4000/health 2>/dev/null || echo "000")
  [ "$code" = "200" ] || [ "$code" = "401" ] && echo "  ✅ litellm :4000 reachable" || echo "  ❌ litellm :4000 (HTTP $code)"
  # acp-bridge
  code=$(curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:18010/health 2>/dev/null || echo "000")
  [ "$code" = "200" ] && echo "  ✅ acp-bridge :18010 healthy" || echo "  ⚠ acp-bridge :18010 (HTTP $code)"
}

case "${1:-}" in
  start)   start_all ;;
  stop)    stop_all ;;
  restart) restart_all ;;
  status)  status_all ;;
  logs)    logs_all "${2:-30}" ;;
  health)  health_check ;;
  *)       usage ;;
esac
