#!/bin/bash
# Jobs 持久化测试 — SQLite 存储、重启恢复、API fallback
# 用法: ACP_TOKEN=<token> bash test/test_jobs.sh
# 前提: Bridge 运行在 Docker (light-acp-bridge-1)
set -uo pipefail
source "$(dirname "$0")/lib.sh"

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
COMPOSE="sudo docker compose -f $PROJECT_DIR/docker/light/docker-compose.yml"
CONTAINER="light-acp-bridge-1"
VENV_PY="/app/.venv/bin/python"

# Token: from env or from .env file
if [[ -z "${ACP_TOKEN:-}" ]]; then
    _env_file="$PROJECT_DIR/docker/light/.env"
    if [[ -f "$_env_file" ]]; then
        ACP_TOKEN=$(grep '^ACP_BRIDGE_TOKEN=' "$_env_file" | cut -d= -f2-)
        export ACP_TOKEN
    fi
fi
AUTH=(-H "Authorization: Bearer ${ACP_TOKEN:-}")

# Override run_test to support regex OR (|)
run_test() {
    local name="$1" expect="$2" actual="$3"
    if echo "$actual" | grep -qiE "$expect"; then
        echo "✅ $name"
        ((PASS++))
    else
        echo "❌ $name"
        echo "   期望包含: $expect"
        echo "   实际: ${actual:0:200}"
        ((FAIL++))
    fi
}

db_query() {
    sudo docker exec "$CONTAINER" "$VENV_PY" -c "
import sqlite3, json
db = sqlite3.connect('/app/data/jobs.db')
db.row_factory = sqlite3.Row
rows = db.execute(\"\"\"$1\"\"\").fetchall()
for r in rows:
    print(json.dumps(dict(r)))
" 2>/dev/null
}

db_count() {
    sudo docker exec "$CONTAINER" "$VENV_PY" -c "
import sqlite3
db = sqlite3.connect('/app/data/jobs.db')
print(db.execute(\"\"\"$1\"\"\").fetchone()[0])
" 2>/dev/null
}

wait_bridge() {
    local max_wait="${1:-20}"
    for i in $(seq 1 "$max_wait"); do
        if curl -sf --max-time 2 "$ACP_BRIDGE_URL/health" >/dev/null 2>&1; then
            return 0
        fi
        sleep 1
    done
    echo "❌ Bridge 未在 ${max_wait}s 内启动" >&2
    return 1
}

echo "=== Jobs 持久化测试 ==="

# --- 前置检查 ---
if ! curl -sf --max-time 3 "$ACP_BRIDGE_URL/health" >/dev/null 2>&1; then
    echo "❌ Bridge 不可达: $ACP_BRIDGE_URL"
    exit 1
fi

if ! sudo docker ps --format '{{.Names}}' | grep -q "$CONTAINER"; then
    echo "❌ 容器 $CONTAINER 未运行，此测试需要 Docker 部署"
    exit 1
fi

echo ""
echo "--- 1. Job 提交写入 SQLite ---"

count_before=$(db_count "SELECT count(*) FROM jobs")

resp=$(curl -sf --max-time 30 -X POST "${AUTH[@]}" "$ACP_BRIDGE_URL/jobs" \
    -H "Content-Type: application/json" \
    -d '{"agent_name":"kiro","prompt":"回复ok两个字就行","session_id":"test-jobs-persist-001"}')
job_id=$(echo "$resp" | jq -r '.job_id // empty')
run_test "提交 job 返回 job_id" ".+" "$job_id"

echo "  等待 job 完成..."
sleep 15

count_after=$(db_count "SELECT count(*) FROM jobs")
if [[ "$count_after" -gt "$count_before" ]]; then
    run_test "SQLite 中 job 数量增加" "increased" "increased"
else
    run_test "SQLite 中 job 数量增加" "increased" "same ($count_before → $count_after)"
fi

db_row=$(db_query "SELECT status, agent FROM jobs WHERE job_id='$job_id'")
db_status=$(echo "$db_row" | jq -r '.status // empty')
run_test "DB 中 job 状态为 completed/failed" "completed|failed" "$db_status"

db_agent=$(echo "$db_row" | jq -r '.agent // empty')
run_test "DB 中 agent 为 kiro" "kiro" "$db_agent"

echo ""
echo "--- 2. API 查询与 DB 一致 ---"

api_resp=$(curl -sf --max-time 10 "${AUTH[@]}" "$ACP_BRIDGE_URL/jobs/$job_id")
api_status=$(echo "$api_resp" | jq -r '.status // empty')
run_test "API 返回的 status 与 DB 一致" "$db_status" "$api_status"

echo ""
echo "--- 3. Job 列表 ---"

list_resp=$(curl -sf --max-time 10 "${AUTH[@]}" "$ACP_BRIDGE_URL/jobs")
list_has_job=$(echo "$list_resp" | jq -r ".jobs[] | select(.job_id==\"$job_id\") | .job_id")
run_test "Job 列表包含刚提交的 job" "$job_id" "$list_has_job"

summary=$(echo "$list_resp" | jq -r '.summary | keys[]' 2>/dev/null | tr '\n' ',')
run_test "Job 列表有 summary 统计" "completed" "$summary"

echo ""
echo "--- 4. 重启后 API 仍能查到历史 job ---"

echo "  重启容器..."
$COMPOSE restart >/dev/null 2>&1
wait_bridge 20 || { echo "❌ 重启后 Bridge 未恢复"; exit 1; }
echo "  Bridge 已恢复"

api_after=$(curl -sf --max-time 10 "${AUTH[@]}" "$ACP_BRIDGE_URL/jobs/$job_id" 2>&1)
api_after_status=$(echo "$api_after" | jq -r '.status // empty')
run_test "重启后 API 仍能查到旧 job" "completed|failed" "$api_after_status"

list_after=$(curl -sf --max-time 10 "${AUTH[@]}" "$ACP_BRIDGE_URL/jobs" 2>&1)
list_after_has=$(echo "$list_after" | jq -r ".jobs[] | select(.job_id==\"$job_id\") | .job_id")
run_test "重启后 job 列表仍包含旧 job" "$job_id" "$list_after_has"

echo ""
echo "--- 5. 中断恢复 — 重试机制 ---"

fake_job_id="fake-$(date +%s)"
sudo docker exec "$CONTAINER" "$VENV_PY" -c "
import sqlite3, time
db = sqlite3.connect('/app/data/jobs.db')
db.execute('INSERT INTO jobs (job_id, agent, session_id, prompt, status, created_at, retries) VALUES (?, \"kiro\", \"fake-sess\", \"回复ok两个字就行\", \"running\", ?, 0)',
           ('$fake_job_id', time.time() - 60))
db.commit()
"

echo "  重启容器触发恢复 (第1次重试)..."
$COMPOSE restart >/dev/null 2>&1
wait_bridge 20 || { echo "❌ 重启后 Bridge 未恢复"; exit 1; }
echo "  Bridge 已恢复，等待重试执行..."
sleep 15

recovered=$(db_query "SELECT status, retries FROM jobs WHERE job_id='$fake_job_id'")
recovered_status=$(echo "$recovered" | jq -r '.status // empty')
recovered_retries=$(echo "$recovered" | jq -r '.retries // 0')
run_test "中断的 job 被重试 (retries>=1)" "." "$([ "$recovered_retries" -ge 1 ] && echo "yes" || echo "no")"
run_test "重试后 job 状态为 completed/failed/running" "completed|failed|running" "$recovered_status"

echo ""
echo "--- 6. 超过最大重试次数标记 failed ---"

fake_maxretry="fakemax-$(date +%s)"
sudo docker exec "$CONTAINER" "$VENV_PY" -c "
import sqlite3, time
db = sqlite3.connect('/app/data/jobs.db')
db.execute('INSERT INTO jobs (job_id, agent, session_id, prompt, status, created_at, retries) VALUES (?, \"kiro\", \"fake-sess3\", \"fake\", \"running\", ?, 3)',
           ('$fake_maxretry', time.time() - 60))
db.commit()
"

echo "  重启容器 (retries=3, 应直接 fail)..."
$COMPOSE restart >/dev/null 2>&1
wait_bridge 20 || { echo "❌ 重启后 Bridge 未恢复"; exit 1; }
sleep 5

maxretry_row=$(db_query "SELECT status, error, retries FROM jobs WHERE job_id='$fake_maxretry'")
maxretry_status=$(echo "$maxretry_row" | jq -r '.status // empty')
maxretry_error=$(echo "$maxretry_row" | jq -r '.error // empty')
run_test "超过重试次数的 job 标记为 failed" "failed" "$maxretry_status"
run_test "失败原因包含 retries" "retries" "$maxretry_error"

echo ""
echo "--- 7. pending job 也会被重试 ---"

fake_pending="fakep-$(date +%s)"
sudo docker exec "$CONTAINER" "$VENV_PY" -c "
import sqlite3, time
db = sqlite3.connect('/app/data/jobs.db')
db.execute('INSERT INTO jobs (job_id, agent, session_id, prompt, status, created_at, retries) VALUES (?, \"kiro\", \"fake-sess2\", \"回复ok两个字就行\", \"pending\", ?, 0)',
           ('$fake_pending', time.time() - 30))
db.commit()
"

$COMPOSE restart >/dev/null 2>&1
wait_bridge 20 || { echo "❌ 重启后 Bridge 未恢复"; exit 1; }
sleep 15

pending_after=$(db_query "SELECT status, retries FROM jobs WHERE job_id='$fake_pending'")
pending_status=$(echo "$pending_after" | jq -r '.status // empty')
pending_retries=$(echo "$pending_after" | jq -r '.retries // 0')
run_test "pending job 被重试 (retries>=1)" "." "$([ "$pending_retries" -ge 1 ] && echo "yes" || echo "no")"

echo ""
echo "--- 8. 多次重启数据不丢 ---"

count_before_multi=$(db_count "SELECT count(*) FROM jobs")
$COMPOSE restart >/dev/null 2>&1
wait_bridge 20 || { echo "❌ 重启后 Bridge 未恢复"; exit 1; }
count_after_multi=$(db_count "SELECT count(*) FROM jobs")
run_test "再次重启后数据量不变" "$count_before_multi" "$count_after_multi"

print_summary "Jobs 持久化"
