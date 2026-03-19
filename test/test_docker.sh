#!/bin/bash
# Docker light 模式测试
# Usage: ACP_TOKEN=<token> bash test/test_docker.sh
set -uo pipefail

PASS=0 FAIL=0
IMAGE="light-acp-bridge"
COMPOSE="docker/light/docker-compose.yml"
BRIDGE="http://127.0.0.1:8001"
REPORT_DIR="$(cd "$(dirname "$0")" && pwd)/reports"
mkdir -p "$REPORT_DIR"
REPORT="$REPORT_DIR/docker-$(date +%Y%m%d-%H%M%S).txt"

run_test() {
    local name="$1" expect="$2" actual="$3"
    if echo "$actual" | grep -qi "$expect"; then
        echo "✅ $name"
        ((PASS++))
    else
        echo "❌ $name"
        echo "   expect: $expect"
        echo "   actual: ${actual:0:200}"
        ((FAIL++))
    fi
}

expect_status() {
    local name="$1" url="$2" code="$3"
    local actual
    actual=$(curl -s -o /dev/null -w "%{http_code}" "$url" 2>/dev/null)
    if [[ "$actual" == "$code" ]]; then
        echo "✅ $name (HTTP $code)"
        ((PASS++))
    else
        echo "❌ $name (expected $code, got $actual)"
        ((FAIL++))
    fi
}

{
echo "=== Docker Light Mode Tests ==="
echo ""

echo "--- Image ---"
resp=$(docker images --format '{{.Repository}}:{{.Tag}}' 2>/dev/null)
run_test "image exists" "$IMAGE" "$resp"
size=$(docker images "$IMAGE" --format '{{.Size}}' 2>/dev/null)
echo "   image size: $size"

echo ""
echo "--- Container ---"
resp=$(docker compose -f "$COMPOSE" ps --format '{{.State}}' 2>/dev/null)
run_test "container running" "running" "$resp"

echo ""
echo "--- Health ---"
resp=$(curl -sf "$BRIDGE/health" 2>/dev/null)
run_test "health endpoint" "ok" "$resp"
version=$(echo "$resp" | python3 -c "import sys,json; print(json.load(sys.stdin).get('version',''))" 2>/dev/null)
echo "   version: $version"

echo ""
echo "--- Auth ---"
expect_status "no token → 401" "$BRIDGE/agents" "401"
if [[ -n "${ACP_TOKEN:-}" ]]; then
    resp=$(curl -sf "$BRIDGE/agents" -H "Authorization: Bearer $ACP_TOKEN" 2>/dev/null)
    run_test "agents endpoint" "agents" "$resp"
    count=$(echo "$resp" | python3 -c "import sys,json; print(len(json.load(sys.stdin).get('agents',[])))" 2>/dev/null)
    echo "   registered agents: $count"
else
    echo "⏭️  skip agent tests (ACP_TOKEN not set)"
fi

echo ""
echo "--- Runtime ---"
container=$(docker compose -f "$COMPOSE" ps -q 2>/dev/null | head -1)
if [[ -n "$container" ]]; then
    py_ver=$(docker exec "$container" python3 --version 2>/dev/null)
    run_test "python available" "Python 3" "$py_ver"
    node_ver=$(docker exec "$container" node --version 2>/dev/null)
    run_test "node available" "v" "$node_ver"
    os=$(docker exec "$container" cat /etc/os-release 2>/dev/null | head -1)
    run_test "debian base" "Debian" "$os"
    user=$(docker exec "$container" whoami 2>/dev/null)
    run_test "non-root user" "app" "$user"
fi

echo ""
echo "=== Results: $PASS passed, $FAIL failed ==="
} 2>&1 | tee "$REPORT"

echo "Report: $REPORT"
FAIL=$(grep -oP '(\d+) failed' "$REPORT" | grep -oP '^\d+')
exit "${FAIL:-0}"
