"""Health and session endpoints."""

import time

import httpx
from fastapi import Path as PathParam
from starlette.responses import JSONResponse

from ..acp_client import AcpProcessPool


def _human_uptime(seconds: int) -> str:
    d, seconds = divmod(seconds, 86400)
    h, seconds = divmod(seconds, 3600)
    m, _ = divmod(seconds, 60)
    parts = []
    if d:
        parts.append(f"{d}d")
    if h:
        parts.append(f"{h}h")
    parts.append(f"{m}m")
    return " ".join(parts)


def register(app, version: str, start_time: float, agents_cfg: dict,
             pool: AcpProcessPool | None, ttl_hours: int,
             job_mgr=None, litellm_cfg: dict | None = None):

    litellm_url = (litellm_cfg or {}).get("url", "")
    litellm_required_by = (litellm_cfg or {}).get("required_by", [])

    @app.get("/health")
    async def health():
        uptime_s = int(time.time() - start_time)

        # --- Pool ---
        if pool:
            pool_stats = pool.stats
            mem_pct = pool._mem_used_pct()
            pool_info = {
                "active": pool_stats["total"],
                "busy": pool_stats["busy"],
                "max": pool._max,
                "max_per_agent": pool._max_per_agent,
                "memory_used_pct": round(mem_pct, 1),
                "memory_limit_pct": pool._memory_limit_pct,
            }
        else:
            pool_stats = {"by_agent": {}}
            mem_pct = 0.0
            pool_info = None

        # --- Agents summary ---
        agents_summary = []
        acp_healthy = 0
        acp_total = 0
        for name, cfg in agents_cfg.items():
            if not isinstance(cfg, dict):
                continue
            mode = cfg.get("mode", "pty")
            alive = pool_stats.get("by_agent", {}).get(name, 0) if pool else 0
            is_healthy = alive > 0 or mode == "pty"
            if mode == "acp":
                acp_total += 1
                if is_healthy:
                    acp_healthy += 1
            agents_summary.append({
                "name": name,
                "mode": mode,
                "enabled": cfg.get("enabled", True),
                "alive": alive,
                "healthy": is_healthy,
            })

        # --- Jobs summary ---
        jobs_info = None
        if job_mgr:
            pending = running = stuck = 0
            now = time.time()
            for j in job_mgr._jobs.values():
                if j.status == "pending":
                    pending += 1
                elif j.status == "running":
                    running += 1
                    if now - j.created_at > 600:
                        stuck += 1
            jobs_info = {"pending": pending, "running": running, "stuck": stuck}

        # --- LiteLLM ---
        litellm_info = None
        if litellm_url:
            try:
                async with httpx.AsyncClient(timeout=3) as client:
                    resp = await client.get(f"{litellm_url}/health/liveliness")
                    reachable = resp.status_code == 200
            except Exception:
                reachable = False
            litellm_info = {
                "url": litellm_url,
                "reachable": reachable,
                "required_by": litellm_required_by,
            }

        # --- Status ---
        status = "ok"
        reasons = []
        if acp_total > 0 and acp_healthy == 0:
            status = "unhealthy"
            reasons.append("no ACP agents alive")
        elif acp_total > 0 and acp_healthy < acp_total:
            status = "degraded"
            reasons.append(f"{acp_total - acp_healthy}/{acp_total} ACP agents down")
        if pool_info and pool_info["active"] >= pool_info["max"]:
            if status == "ok":
                status = "degraded"
            reasons.append("pool exhausted")
        if pool_info and mem_pct >= pool_info["memory_limit_pct"]:
            if status == "ok":
                status = "degraded"
            reasons.append(f"memory {mem_pct:.0f}% >= {pool_info['memory_limit_pct']}% limit")
        if litellm_info and not litellm_info["reachable"] and litellm_required_by:
            if status == "ok":
                status = "degraded"
            reasons.append("litellm unreachable")
        if jobs_info and jobs_info["stuck"] > 0:
            if status == "ok":
                status = "degraded"
            reasons.append(f"{jobs_info['stuck']} stuck job(s)")

        body = {
            "status": status,
            "version": version,
            "uptime": uptime_s,
            "uptime_human": _human_uptime(uptime_s),
            "agents": agents_summary,
        }
        if pool_info:
            body["pool"] = pool_info
        if jobs_info is not None:
            body["jobs"] = jobs_info
        if litellm_info:
            body["litellm"] = litellm_info
        if reasons:
            body["reasons"] = reasons

        status_code = 503 if status == "unhealthy" else 200
        return JSONResponse(content=body, status_code=status_code)

    @app.get("/health/agents")
    async def health_agents():
        stats = pool.stats if pool else {"by_agent": {}}
        agent_list = []
        for name, cfg in agents_cfg.items():
            if not isinstance(cfg, dict):
                continue
            alive = stats["by_agent"].get(name, 0)
            sessions = []
            if pool:
                for (a, sid), conn in pool._connections.items():
                    if a == name:
                        sessions.append({
                            "session_id": sid,
                            "alive": conn.alive,
                            "state": conn.state,
                            "idle": round(time.time() - conn.last_active, 1),
                        })
            agent_list.append({
                "name": name,
                "mode": cfg.get("mode", "pty"),
                "alive_sessions": alive,
                "healthy": alive > 0 or cfg.get("mode") == "pty",
                "sessions": sessions,
            })
        return {"version": version, "agents": agent_list}

    if pool:
        @app.delete("/sessions/{agent}/{session_id}")
        async def delete_session(agent: str = PathParam(...), session_id: str = PathParam(...)):
            await pool.close(agent, session_id)
            return {"status": "closed", "agent": agent, "session_id": session_id}
