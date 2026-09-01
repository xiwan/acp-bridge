"""LiteLLM proxy — transparent pass-through with usage recording."""

import asyncio
import logging
import sqlite3
import time
from pathlib import Path

import httpx
from starlette.requests import Request
from starlette.responses import JSONResponse

log = logging.getLogger("acp-bridge.litellm-proxy")

_DB_PATH = "data/usage.db"
_SCHEMA = """
CREATE TABLE IF NOT EXISTS llm_usage (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    ts           REAL NOT NULL,
    model        TEXT NOT NULL,
    input_tokens INTEGER DEFAULT 0,
    output_tokens INTEGER DEFAULT 0,
    total_tokens INTEGER DEFAULT 0,
    cached_tokens INTEGER DEFAULT 0,
    cache_creation_tokens INTEGER DEFAULT 0,
    cost_usd     REAL DEFAULT 0.0,
    duration     REAL DEFAULT 0.0
);
CREATE INDEX IF NOT EXISTS idx_usage_ts ON llm_usage(ts);
"""


def _get_db():
    Path(_DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(_DB_PATH, check_same_thread=False)
    db.execute("PRAGMA journal_mode=WAL")
    db.row_factory = sqlite3.Row
    db.executescript(_SCHEMA)
    return db


_db = None


def _ensure_db():
    global _db
    if _db is None:
        _db = _get_db()
    return _db


def delete_old(max_age: float = 30 * 86400) -> int:
    """Prune llm_usage rows older than max_age seconds. Called by main's cleanup loop."""
    db = _ensure_db()
    cur = db.execute("DELETE FROM llm_usage WHERE ts < ?", (time.time() - max_age,))
    db.commit()
    return cur.rowcount


def _record_usage(model: str, usage: dict, duration: float):
    db = _ensure_db()
    prompt_details = usage.get("prompt_tokens_details") or {}
    db.execute(
        """INSERT INTO llm_usage (ts, model, input_tokens, output_tokens, total_tokens,
           cached_tokens, cache_creation_tokens, duration)
           VALUES (?,?,?,?,?,?,?,?)""",
        (time.time(), model,
         usage.get("prompt_tokens", 0),
         usage.get("completion_tokens", 0),
         usage.get("total_tokens", 0),
         prompt_details.get("cached_tokens", 0) or usage.get("cache_read_input_tokens", 0),
         prompt_details.get("cache_creation_tokens", 0) or usage.get("cache_creation_input_tokens", 0),
         duration))
    db.commit()


def register(app, litellm_cfg: dict):
    url = litellm_cfg.get("url", "http://localhost:4000")
    api_key = litellm_cfg.get("env", {}).get("LITELLM_API_KEY", "")
    # Reused client (connection pooling) — same pattern as routes/tools.py.
    client = httpx.AsyncClient(timeout=120)

    @app.api_route("/litellm/{path:path}", methods=["GET", "POST"])
    async def litellm_proxy(request: Request, path: str):
        """Transparent proxy to LiteLLM — records usage from chat/completions responses."""
        target = f"{url}/{path}"
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        if request.method == "GET":
            resp = await client.get(target, headers=headers, params=dict(request.query_params))
        else:
            body = await request.body()
            resp = await client.post(target, headers=headers, content=body)
        try:
            data = resp.json()
        except Exception:
            return JSONResponse({"raw": resp.text}, status_code=resp.status_code)

        # Usage recording is handled by LiteLLM callback → /internal/llm-callback
        return JSONResponse(data, status_code=resp.status_code)

    @app.get("/usage")
    async def get_usage(hours: float = 24, model: str = ""):
        """Query recorded LLM usage stats.

        v0.23.0: 加 cost_usd 字段 (per-model + grand total) 基于 LiteLLM pricing 表;
                 by_model SQL 加 SUM(cache_creation_tokens) 修历史漏报.
        """
        from ..cost import calc_cost_v2  # 局部 import 避免顶部循环依赖
        db = _ensure_db()
        cutoff = time.time() - hours * 3600
        where, params = ["ts > ?"], [cutoff]
        if model:
            where.append("model = ?")
            params.append(model)
        w = " AND ".join(where)

        row = db.execute(
            f"""SELECT COUNT(*) as calls,
                       COALESCE(SUM(input_tokens),0) as input_tokens,
                       COALESCE(SUM(output_tokens),0) as output_tokens,
                       COALESCE(SUM(total_tokens),0) as total_tokens,
                       COALESCE(SUM(cached_tokens),0) as cached_tokens,
                       COALESCE(SUM(cache_creation_tokens),0) as cache_creation_tokens,
                       COALESCE(AVG(duration),0) as avg_duration
                FROM llm_usage WHERE {w}""", params).fetchone()

        total_input = row["input_tokens"]
        cached = row["cached_tokens"]
        cache_rate = (cached / total_input * 100) if total_input > 0 else 0.0

        models = db.execute(
            f"""SELECT model, COUNT(*) as calls,
                       SUM(input_tokens) as input_tokens,
                       SUM(output_tokens) as output_tokens,
                       SUM(cached_tokens) as cached_tokens,
                       SUM(cache_creation_tokens) as cache_creation_tokens
                FROM llm_usage WHERE {w} GROUP BY model ORDER BY calls DESC""",
            params).fetchall()

        # v0.23.0: per-model cost
        by_model = []
        total_cost = 0.0
        for m in models:
            m_dict = dict(m)
            cost = calc_cost_v2(
                m_dict["model"],
                m_dict.get("input_tokens", 0) or 0,
                m_dict.get("cached_tokens", 0) or 0,
                m_dict.get("cache_creation_tokens", 0) or 0,
                m_dict.get("output_tokens", 0) or 0,
            )
            m_dict["cost_usd"] = round(cost, 4)
            total_cost += cost
            by_model.append(m_dict)

        return {
            "hours": hours,
            "calls": row["calls"],
            "input_tokens": total_input,
            "output_tokens": row["output_tokens"],
            "total_tokens": row["total_tokens"],
            "cached_tokens": cached,
            "cache_creation_tokens": row["cache_creation_tokens"],
            "cache_rate_pct": round(cache_rate, 1),
            "avg_duration_s": round(row["avg_duration"], 2),
            "total_cost_usd": round(total_cost, 4),
            "by_model": by_model,
        }

    @app.get("/usage/recent")
    async def get_usage_recent(limit: int = 20):
        db = _ensure_db()
        rows = db.execute(
            "SELECT * FROM llm_usage ORDER BY ts DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]

    @app.post("/internal/llm-callback")
    async def litellm_callback(request: Request):
        """Receive StandardLoggingPayload from LiteLLM generic_api callback."""
        body = await request.json()
        entries = body if isinstance(body, list) else [body]
        recorded = 0
        for entry in entries:
            model = entry.get("model") or entry.get("model_id") or ""
            # StandardLoggingPayload: tokens at top level
            input_tokens = entry.get("prompt_tokens") or entry.get("total_tokens", 0)
            output_tokens = entry.get("completion_tokens", 0)
            total_tokens = entry.get("total_tokens", 0)
            # Cache tokens: direct (custom callback) or hidden_params (generic_api)
            cached = entry.get("cached_tokens", 0) or 0
            cache_creation = entry.get("cache_creation_tokens", 0) or 0
            if not cached and not cache_creation:
                usage_obj = (entry.get("hidden_params") or {}).get("usage_object") or {}
                ptd = (usage_obj.get("prompt_tokens_details") or {})
                cached = ptd.get("cached_tokens", 0) or 0
                cache_creation = usage_obj.get("cache_creation_input_tokens", 0) or 0
            # Duration
            duration = 0.0
            if entry.get("response_time"):
                duration = float(entry["response_time"])
            else:
                start = entry.get("startTime") or entry.get("start_time") or ""
                end = entry.get("endTime") or entry.get("end_time") or ""
                try:
                    from datetime import datetime
                    t0 = datetime.fromisoformat(str(start).replace("Z", "+00:00"))
                    t1 = datetime.fromisoformat(str(end).replace("Z", "+00:00"))
                    duration = (t1 - t0).total_seconds()
                except Exception:
                    pass
            try:
                def _insert(m=model, it=input_tokens, ot=output_tokens,
                            tt=total_tokens, c=cached, cc=cache_creation, d=duration):
                    db = _ensure_db()
                    db.execute(
                        """INSERT INTO llm_usage (ts, model, input_tokens, output_tokens, total_tokens,
                           cached_tokens, cache_creation_tokens, duration)
                           VALUES (?,?,?,?,?,?,?,?)""",
                        (time.time(), m, it, ot, tt, c, cc, d))
                    db.commit()
                await asyncio.to_thread(_insert)
                recorded += 1
            except Exception as e:
                log.warning("callback_record_failed: %s", e)
        log.info("llm_callback: recorded=%d/%d", recorded, len(entries))
        return {"recorded": recorded}
