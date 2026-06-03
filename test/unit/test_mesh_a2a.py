"""Unit tests for src/mesh_a2a.py — A2A Mesh L1 (POST /a2a adapter)."""

import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from acp_sdk.models import Message, MessagePart
from src.mesh_a2a import A2AAdapter, _a2a_parts_to_acp


class _FakeAgent:
    """Mimics app.state.acp_agents[name]: run(input, context) async-gen, ignores context."""
    def __init__(self, name): self.name = name
    async def run(self, input, context):
        prompt = "".join(p.content for m in input for p in m.parts if p.content)
        yield MessagePart(content=f"echo:{prompt}", content_type="text/plain")
        yield Message(parts=[MessagePart(content="!", content_type="text/plain")])


class _BoomAgent:
    async def run(self, input, context):
        raise RuntimeError("kaboom")
        yield  # pragma: no cover


class _FakeJob:
    def __init__(self, status, result=None):
        self.status = status; self.result = result


class _FakeJobMgr:
    def __init__(self, jobs): self._jobs = jobs
    def get(self, jid): return self._jobs.get(jid)


def _adapter(agents=None, jobs=None):
    return A2AAdapter(agents_provider=lambda: agents or {},
                      job_mgr=_FakeJobMgr(jobs or {}))


def _send(skill, text):
    return {"jsonrpc": "2.0", "id": 1, "method": "tasks/send",
            "params": {"skill": skill, "message": {"parts": [{"type": "text", "text": text}]}}}


def test_a2a_parts_to_acp_text_only():
    msgs = _a2a_parts_to_acp({"parts": [{"type": "text", "text": "hi"},
                                        {"type": "text", "text": " there"}]})
    assert len(msgs) == 1
    assert [p.content for p in msgs[0].parts] == ["hi", " there"]


@pytest.mark.asyncio
async def test_tasks_send_happy_path():
    a = _adapter(agents={"kiro": _FakeAgent("kiro")})
    resp = await a.dispatch(_send("kiro", "hello"))
    assert resp["result"]["status"]["state"] == "completed"
    assert resp["result"]["artifacts"][0]["parts"][0]["text"] == "echo:hello!"
    # billing reservation present, free in L1
    assert resp["result"]["metadata"]["cost"]["amount"] == 0
    assert resp["result"]["metadata"]["usage"] is None


@pytest.mark.asyncio
async def test_tasks_send_unknown_skill():
    a = _adapter(agents={"kiro": _FakeAgent("kiro")})
    resp = await a.dispatch(_send("ghost", "x"))
    assert resp["error"]["code"] == -32601


@pytest.mark.asyncio
async def test_tasks_send_agent_error():
    a = _adapter(agents={"boom": _BoomAgent()})
    resp = await a.dispatch(_send("boom", "x"))
    assert resp["error"]["code"] == -32000


@pytest.mark.asyncio
async def test_unknown_method():
    a = _adapter()
    resp = await a.dispatch({"jsonrpc": "2.0", "id": 9, "method": "tasks/dance", "params": {}})
    assert resp["error"]["code"] == -32601


@pytest.mark.asyncio
async def test_tasks_get_completed():
    a = _adapter(jobs={"j1": _FakeJob("completed", "the result")})
    resp = await a.dispatch({"jsonrpc": "2.0", "id": 2, "method": "tasks/get",
                             "params": {"id": "j1"}})
    assert resp["result"]["status"]["state"] == "completed"
    assert resp["result"]["artifacts"][0]["parts"][0]["text"] == "the result"


@pytest.mark.asyncio
async def test_tasks_get_not_found():
    a = _adapter(jobs={})
    resp = await a.dispatch({"jsonrpc": "2.0", "id": 3, "method": "tasks/get",
                             "params": {"id": "nope"}})
    assert resp["error"]["code"] == -32001
