"""Agent pool management — lifecycle, health checks, status."""

import asyncio
import logging
import threading

from .acp_client import AcpProcessPool
from .fallback_policy import _agent_healthy, _state_lock

log = logging.getLogger("acp-bridge.agent_pool")

_pool_lock = threading.RLock()
_pool: AcpProcessPool | None = None


def init_pool(pool: AcpProcessPool) -> None:
    """Set the global pool instance (called once at startup)."""
    global _pool
    with _pool_lock:
        _pool = pool


def get_pool() -> AcpProcessPool | None:
    """Return the current pool instance."""
    with _pool_lock:
        return _pool


def shutdown_pool() -> None:
    """Clear the global pool reference."""
    global _pool
    with _pool_lock:
        _pool = None


def get_pool_status() -> dict:
    """Return pool stats or empty dict if no pool."""
    with _pool_lock:
        if _pool is None:
            return {}
        return _pool.stats


async def ping_agent(pool: AcpProcessPool, agent: str, timeout: float = 5) -> bool:
    """Ping all idle connections for an agent. Returns True if at least one responds."""
    for (a, _), conn in list(pool._connections.items()):
        if a == agent and conn.state == "idle":
            if await conn.ping(timeout=timeout):
                return True
    return False


async def ping_loop(pool: AcpProcessPool, interval: float = 300):
    """Background task: periodically ping idle agents, update _agent_healthy."""
    while True:
        await asyncio.sleep(interval)
        pinged_agents: set[str] = set()
        for (agent, _), conn in list(pool._connections.items()):
            if agent in pinged_agents:
                continue
            if conn.state != "idle":
                continue
            pinged_agents.add(agent)
            ok = await ping_agent(pool, agent)
            with _state_lock:
                _agent_healthy[agent] = ok
            if not ok:
                log.warning("ping_unhealthy: agent=%s marked unhealthy", agent)
            else:
                log.debug("ping_healthy: agent=%s", agent)
        # Reset agents no longer in pool to healthy (optimistic default)
        with _state_lock:
            for agent in list(_agent_healthy):
                if not _agent_healthy[agent] and agent not in pinged_agents:
                    _agent_healthy.pop(agent)
                    log.info("ping_reset: agent=%s no longer in pool, cleared unhealthy mark", agent)
