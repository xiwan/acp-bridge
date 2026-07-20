"""Health probe and cold-start state tests."""

import time

import httpx
import pytest
from fastapi import FastAPI

from src.fallback_policy import _agent_healthy, _state_lock
from src.routes import health as health_routes


class FakePool:
    def __init__(self, by_agent=None):
        self._by_agent = by_agent or {}
        self._connections = {}
        self._max = 12
        self._max_per_agent = 3
        self._memory_limit_pct = 75

    @property
    def stats(self):
        return {
            "total": sum(self._by_agent.values()),
            "busy": 0,
            "by_agent": self._by_agent,
        }

    @staticmethod
    def _mem_used_pct():
        return 25.0


@pytest.fixture(autouse=True)
def clear_agent_health():
    with _state_lock:
        _agent_healthy.clear()
    yield
    with _state_lock:
        _agent_healthy.clear()


def make_app(agents_cfg, pool=None):
    app = FastAPI()
    app.state.acp_agents = {}
    health_routes.register(
        app, "test-version", time.time(), agents_cfg, pool, ttl_hours=24,
    )
    return app


@pytest.mark.asyncio
async def test_cold_start_is_ready_and_healthy():
    app = make_app({
        "kiro": {"mode": "acp", "enabled": True},
        "trae": {"mode": "pty", "enabled": True},
    }, FakePool())
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        live = await client.get("/live")
        ready = await client.get("/ready")
        health = await client.get("/health")
        details = await client.get("/health/agents")

    assert live.status_code == 200
    assert live.json()["status"] == "alive"
    assert ready.status_code == 200
    assert ready.json()["status"] == "ready"
    assert ready.json()["pool_state"] == "cold"
    assert health.status_code == 200
    assert health.json()["status"] == "ok"
    states = {a["name"]: a["state"] for a in health.json()["agents"]}
    assert states == {"kiro": "cold", "trae": "on_demand"}
    detail_states = {a["name"]: a["state"] for a in details.json()["agents"]}
    assert detail_states["kiro"] == "cold"


@pytest.mark.asyncio
async def test_explicitly_unhealthy_agent_is_down():
    with _state_lock:
        _agent_healthy["kiro"] = False
    app = make_app({"kiro": {"mode": "acp", "enabled": True}}, FakePool())
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/health")

    assert response.status_code == 503
    assert response.json()["status"] == "unhealthy"
    assert response.json()["agents"][0]["state"] == "down"


@pytest.mark.asyncio
async def test_alive_but_unresponsive_agent_is_down():
    with _state_lock:
        _agent_healthy["kiro"] = False
    app = make_app(
        {"kiro": {"mode": "acp", "enabled": True}},
        FakePool({"kiro": 1}),
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/health")

    assert response.status_code == 503
    assert response.json()["status"] == "unhealthy"
    assert response.json()["agents"][0]["alive"] == 1
    assert response.json()["agents"][0]["state"] == "down"


@pytest.mark.asyncio
async def test_mixed_cold_and_down_agents_are_degraded():
    with _state_lock:
        _agent_healthy["qwen"] = False
    app = make_app({
        "kiro": {"mode": "acp", "enabled": True},
        "qwen": {"mode": "acp", "enabled": True},
    }, FakePool())
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "degraded"
    assert response.json()["agent_states"]["cold"] == 1
    assert response.json()["agent_states"]["down"] == 1


@pytest.mark.asyncio
async def test_ready_requires_a_configured_agent():
    app = make_app({}, FakePool())
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/ready")

    assert response.status_code == 503
    assert response.json()["status"] == "not_ready"
