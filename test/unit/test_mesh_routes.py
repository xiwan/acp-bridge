"""HTTP-level tests for POST /a2a and /a2a/announce (src/routes/mesh.py).

No prior test hit these routes as real HTTP requests: test_mesh_a2a.py only
calls A2AAdapter.dispatch() directly, bypassing the route layer entirely —
so the mesh.token Bearer check itself (the CRITICAL finding's actual attack
surface) had zero coverage. These prove it: with mesh.token configured,
requests without a matching Bearer header are refused.

The startup-time fail-closed guard (mesh.enabled=true + empty token refuses
to boot) is covered separately in test_mesh.py::resolve_mesh_token — that's
a config-resolution concern, not something this route-level HTTP layer can
observe (an empty token here is deliberately permissive by design; main.py
is what refuses to wire this route with an empty token in the first place).
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import httpx
import pytest
from fastapi import FastAPI

from src.routes import mesh as mesh_routes


class _FakeMesh:
    """Minimal stand-in for MeshManager — only the attrs/methods routes/mesh.py touches."""

    def __init__(self, token=""):
        self.token = token
        self.self_url = "http://node-a:18010"

    def build_agent_card(self):
        return {"name": "acp-bridge@test", "skills": []}

    def record_peer(self, card, peers):
        pass

    def known_peers(self):
        return []

    def peers_view(self):
        return {}


class _FakeAdapter:
    async def dispatch(self, rpc, inbound_hop=False):
        return {"jsonrpc": "2.0", "id": rpc.get("id"), "result": {"ok": True}}


def _app(token="", with_adapter=True):
    app = FastAPI()
    mesh_routes.register(
        app, _FakeMesh(token=token), adapter=_FakeAdapter() if with_adapter else None
    )
    return app


@pytest.mark.asyncio
async def test_agent_card_is_public():
    app = _app(token="s3cr3t")
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/.well-known/agent.json")
    assert resp.status_code == 200
    assert resp.json()["name"] == "acp-bridge@test"


@pytest.mark.asyncio
async def test_a2a_announce_rejects_missing_or_wrong_token():
    app = _app(token="s3cr3t")
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        no_auth = await client.post("/a2a/announce", json={"agent_card": {"url": "http://peer"}})
        wrong_auth = await client.post(
            "/a2a/announce",
            json={"agent_card": {"url": "http://peer"}},
            headers={"Authorization": "Bearer wrong"},
        )
    assert no_auth.status_code == 401
    assert wrong_auth.status_code == 401


@pytest.mark.asyncio
async def test_a2a_announce_accepts_correct_token():
    app = _app(token="s3cr3t")
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/a2a/announce",
            json={"agent_card": {"url": "http://peer"}, "peers": []},
            headers={"Authorization": "Bearer s3cr3t"},
        )
    assert resp.status_code == 200
    assert resp.json()["agent_card"]["name"] == "acp-bridge@test"


@pytest.mark.asyncio
async def test_a2a_rpc_rejects_missing_or_wrong_token():
    app = _app(token="s3cr3t")
    transport = httpx.ASGITransport(app=app)
    rpc = {"jsonrpc": "2.0", "id": 1, "method": "tasks/send", "params": {}}
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        no_auth = await client.post("/a2a", json=rpc)
        wrong_auth = await client.post("/a2a", json=rpc, headers={"Authorization": "Bearer wrong"})
    assert no_auth.status_code == 401
    assert wrong_auth.status_code == 401


@pytest.mark.asyncio
async def test_a2a_rpc_accepts_correct_token():
    app = _app(token="s3cr3t")
    transport = httpx.ASGITransport(app=app)
    rpc = {"jsonrpc": "2.0", "id": 1, "method": "tasks/send", "params": {}}
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/a2a", json=rpc, headers={"Authorization": "Bearer s3cr3t"})
    assert resp.status_code == 200
    assert resp.json()["result"]["ok"] is True


@pytest.mark.asyncio
async def test_a2a_rpc_404_without_adapter():
    app = _app(token="s3cr3t", with_adapter=False)
    transport = httpx.ASGITransport(app=app)
    rpc = {"jsonrpc": "2.0", "id": 1, "method": "tasks/send", "params": {}}
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/a2a", json=rpc, headers={"Authorization": "Bearer s3cr3t"})
    assert resp.status_code == 404
