"""Health and session endpoints."""

import time

from fastapi import Path as PathParam

from ..acp_client import AcpProcessPool


def register(app, version: str, start_time: float, agents_cfg: dict,
             pool: AcpProcessPool | None, ttl_hours: int):

    @app.get("/health")
    async def health():
        return {"status": "ok", "version": version, "uptime": int(time.time() - start_time)}

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
