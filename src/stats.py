"""Agent call statistics — collect, persist, query."""

import json
import sqlite3
import threading
import time
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS agent_stats (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    agent        TEXT NOT NULL,
    session_id   TEXT NOT NULL,
    success      INTEGER NOT NULL,
    duration     REAL NOT NULL,
    tool_count   INTEGER DEFAULT 0,
    tools        TEXT DEFAULT '[]',
    created_at   REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_stats_agent ON agent_stats(agent);
CREATE INDEX IF NOT EXISTS idx_stats_agent_created ON agent_stats(agent, created_at);
CREATE INDEX IF NOT EXISTS idx_stats_created ON agent_stats(created_at);

CREATE TABLE IF NOT EXISTS fallback_stats (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    original_agent  TEXT NOT NULL,
    fallback_agent  TEXT NOT NULL,
    tried_agents    TEXT DEFAULT '[]',
    success         INTEGER NOT NULL,
    created_at      REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_fb_original ON fallback_stats(original_agent);
CREATE INDEX IF NOT EXISTS idx_fb_created ON fallback_stats(created_at);
"""


class StatsCollector:
    def __init__(self, db_path: str = "data/jobs.db"):
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(db_path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.executescript(_SCHEMA)
        self._lock = threading.Lock()

    def record(self, agent: str, session_id: str, success: bool,
               duration: float, tools: list[str] | None = None):
        tools = tools or []
        with self._lock:
            self._db.execute(
                "INSERT INTO agent_stats (agent, session_id, success, duration, tool_count, tools, created_at) "
                "VALUES (?,?,?,?,?,?,?)",
                (agent, session_id, int(success), duration, len(tools),
                 json.dumps(tools), time.time()),
            )
            self._db.commit()

    def query(self, agent: str | None = None, hours: float = 24) -> dict:
        cutoff = time.time() - hours * 3600
        if agent:
            rows = self._db.execute(
                "SELECT * FROM agent_stats WHERE agent = ? AND created_at > ?",
                (agent, cutoff),
            ).fetchall()
        else:
            rows = self._db.execute(
                "SELECT * FROM agent_stats WHERE created_at > ?", (cutoff,),
            ).fetchall()

        agents: dict[str, dict] = {}
        for r in rows:
            a = r["agent"]
            if a not in agents:
                agents[a] = {"total": 0, "success": 0, "failed": 0,
                             "durations": [], "tools": {}}
            s = agents[a]
            s["total"] += 1
            if r["success"]:
                s["success"] += 1
            else:
                s["failed"] += 1
            s["durations"].append(r["duration"])
            for t in json.loads(r["tools"]):
                # Aggregate by tool category (first token before ':' or space)
                # e.g. "Running: cd ..." → "Running", "Reading a.py:1" → "Reading"
                cat = t.split(":", 1)[0].split(" ", 1)[0] or t
                s["tools"][cat] = s["tools"].get(cat, 0) + 1

        result = {}
        for a, s in agents.items():
            result[a] = {
                "total": s["total"],
                "success": s["success"],
                "failed": s["failed"],
                "avg_duration": round(sum(s["durations"]) / len(s["durations"]), 2),
                "max_duration": round(max(s["durations"]), 2),
                "tools_used": dict(sorted(s["tools"].items(), key=lambda x: -x[1])[:10]),
            }
        return {"period_hours": hours, "agents": result}

    def get_agent_stats(self, agent: str, hours: float = 1.0) -> dict:
        """Per-agent stats for a time window. Returns {success_rate, avg_duration, total}."""
        cutoff = time.time() - hours * 3600
        rows = self._db.execute(
            "SELECT success, duration FROM agent_stats WHERE agent = ? AND created_at > ?",
            (agent, cutoff),
        ).fetchall()
        if not rows:
            return {"success_rate": 1.0, "avg_duration": 30.0, "total": 0}
        total = len(rows)
        success = sum(1 for r in rows if r["success"])
        avg_dur = sum(r["duration"] for r in rows) / total
        return {"success_rate": success / total, "avg_duration": round(avg_dur, 2), "total": total}

    def delete_old(self, max_age: float = 7 * 86400) -> int:
        cutoff = time.time() - max_age
        with self._lock:
            cur = self._db.execute(
                "DELETE FROM agent_stats WHERE created_at < ?", (cutoff,),
            )
            cur2 = self._db.execute(
                "DELETE FROM fallback_stats WHERE created_at < ?", (cutoff,),
            )
            self._db.commit()
            return cur.rowcount + cur2.rowcount

    def record_fallback(self, original_agent: str, fallback_agent: str,
                        tried_agents: list[str], success: bool):
        with self._lock:
            self._db.execute(
                "INSERT INTO fallback_stats (original_agent, fallback_agent, tried_agents, success, created_at) "
                "VALUES (?,?,?,?,?)",
                (original_agent, fallback_agent, json.dumps(tried_agents),
                 int(success), time.time()),
            )
            self._db.commit()

    def query_fallback(self, hours: float = 24) -> dict:
        cutoff = time.time() - hours * 3600
        rows = self._db.execute(
            "SELECT * FROM fallback_stats WHERE created_at > ?", (cutoff,),
        ).fetchall()

        total = len(rows)
        success = sum(1 for r in rows if r["success"])
        by_agent: dict[str, dict] = {}
        routes: dict[str, int] = {}

        for r in rows:
            orig = r["original_agent"]
            fb = r["fallback_agent"]
            if orig not in by_agent:
                by_agent[orig] = {"total": 0, "success": 0, "fallback_to": {}}
            by_agent[orig]["total"] += 1
            if r["success"]:
                by_agent[orig]["success"] += 1
            by_agent[orig]["fallback_to"][fb] = by_agent[orig]["fallback_to"].get(fb, 0) + 1
            route = f"{orig}→{fb}"
            routes[route] = routes.get(route, 0) + 1

        return {
            "period_hours": hours,
            "total": total,
            "success": success,
            "failed": total - success,
            "by_agent": by_agent,
            "top_routes": dict(sorted(routes.items(), key=lambda x: -x[1])[:10]),
        }
