"""Session list/resume endpoints for ACP agents that support session persistence."""

import logging
from fastapi import HTTPException, Query
from ..acp_client import AcpProcessPool

log = logging.getLogger("acp-bridge.sessions")


def register(app, pool: AcpProcessPool, agents_cfg: dict):

    @app.get("/agents/{agent_name}/sessions")
    async def list_sessions(
        agent_name: str,
        cwd: str = Query("", description="Working directory to list sessions for"),
        cursor: int | None = Query(None, description="Pagination cursor"),
        size: int = Query(20, ge=1, le=100),
    ):
        """List persisted sessions for an agent (requires agent support for session/list)."""
        cfg = agents_cfg.get(agent_name)
        if not cfg or not isinstance(cfg, dict):
            raise HTTPException(404, f"agent not found: {agent_name}")
        if cfg.get("mode") != "acp":
            raise HTTPException(400, f"agent {agent_name} does not support sessions (not ACP mode)")

        effective_cwd = cwd or cfg.get("working_dir", "/tmp")

        # Need a live connection to query sessions — get or spawn one
        import uuid
        temp_session = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"{agent_name}:session-list"))
        try:
            conn = await pool.get_or_create(agent_name, temp_session, cwd=effective_cwd)
            result = await conn.session_list(effective_cwd, cursor=cursor, size=size)
            return result
        except Exception as e:
            log.warning("session_list failed: agent=%s error=%s", agent_name, e)
            raise HTTPException(502, f"session/list failed: {e}")
