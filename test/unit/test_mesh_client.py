"""Unit tests for src/mesh_client.py + L2 hop limit — A2A Mesh L2 routing."""

import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from acp_sdk.models import Message, MessagePart
from src.mesh_client import make_a2a_remote_handler, reconcile
from src.mesh_a2a import A2AAdapter


class _Peer:
    def __init__(self, url, skills, healthy=True):
        self.url = url; self.skills = skills; self.healthy = healthy


class _Mesh:
    def __init__(self, local, peers, token="m"):
        self._local = local; self._peers = {p.url: p for p in peers}; self.token = token
    def _agent_names(self): return self._local


class _AppState:
    def __init__(self, agents): self.acp_agents = agents


class _App:
    def __init__(self, agents): self.state = _AppState(agents)


def test_reconcile_registers_peer_only_skill():
    mesh = _Mesh(local=["kiro"], peers=[_Peer("http://b:18010", ["kiro", "claude"])])
    app = _App({"kiro": object()})  # kiro is local
    rs = set()
    added = reconcile(app, mesh, rs)
    assert added == ["claude"]            # only the peer-only skill
    assert "claude" in app.state.acp_agents
    assert "claude" in rs
    assert "kiro" in app.state.acp_agents and "kiro" not in rs  # local untouched


def test_reconcile_local_priority():
    # peer also has kiro; local kiro must not be shadowed
    mesh = _Mesh(local=["kiro"], peers=[_Peer("http://b", ["kiro"])])
    local_kiro = object()
    app = _App({"kiro": local_kiro})
    added = reconcile(app, mesh, set())
    assert added == []
    assert app.state.acp_agents["kiro"] is local_kiro


def test_reconcile_skips_unhealthy_peer():
    mesh = _Mesh(local=[], peers=[_Peer("http://b", ["claude"], healthy=False)])
    app = _App({})
    assert reconcile(app, mesh, set()) == []


def test_reconcile_idempotent():
    mesh = _Mesh(local=[], peers=[_Peer("http://b", ["claude"])])
    app = _App({}); rs = set()
    assert reconcile(app, mesh, rs) == ["claude"]
    assert reconcile(app, mesh, rs) == []  # already registered, no dup


@pytest.mark.asyncio
async def test_remote_handler_error_yields_message(monkeypatch):
    # No real peer -> httpx fails -> handler yields a [remote error] part (no raise)
    h = make_a2a_remote_handler("claude", "http://127.0.0.1:1", "tok")
    out = []
    async for y in h([Message(parts=[MessagePart(content="hi", content_type="text/plain")])], None):
        out.append(y.content)
    assert any("remote error" in c for c in out)


@pytest.mark.asyncio
async def test_hop_limit_refuses_second_hop():
    # inbound request already hopped; target skill is remote -> refuse
    rs = {"claude"}
    a = A2AAdapter(agents_provider=lambda: {"claude": object()}, remote_skills=rs)
    resp = await a.dispatch(
        {"jsonrpc": "2.0", "id": 1, "method": "tasks/send",
         "params": {"skill": "claude", "message": {"parts": []}}},
        inbound_hop=True)
    assert resp["error"]["code"] == -32011


@pytest.mark.asyncio
async def test_hop_limit_allows_local_for_hopped_request():
    # inbound hopped, but target is a LOCAL skill (not in remote_skills) -> allowed
    class _FakeLocal:
        async def run(self, input, context):
            yield MessagePart(content="ok", content_type="text/plain")
    a = A2AAdapter(agents_provider=lambda: {"kiro": _FakeLocal()}, remote_skills={"claude"})
    resp = await a.dispatch(
        {"jsonrpc": "2.0", "id": 2, "method": "tasks/send",
         "params": {"skill": "kiro", "message": {"parts": [{"type": "text", "text": "x"}]}}},
        inbound_hop=True)
    assert resp["result"]["status"]["state"] == "completed"
