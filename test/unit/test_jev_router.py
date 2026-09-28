"""Unit tests for src/jev_router.py + src/routes/router.py (v0.47.0).

Jev is mocked with httpx.MockTransport — no network. Covers: criteria construction
and candidate filtering, threshold / other / invalid / error degradation to the
default agent, prompt truncation, auth header, key never leaking, the virtual-agent
handler's route_info + delegation, and the /route/* endpoints.
"""

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import httpx
import pytest
from acp_sdk.models import Message, MessagePart
from fastapi import FastAPI

from src.jev_router import OTHER, JevRouter, RouteDecision, make_router_agent_handler
from src.routes import router as router_routes

KEY = "apikey_TESTONLY_0000000000000000000000"

AGENTS = {
    "kiro": {
        "enabled": True,
        "mode": "acp",
        "trust": "workspace",
        "description": "Kiro CLI agent",
        "capabilities": {
            "domains": ["devops", "cloud"],
            "tags": ["cli-first"],
            "tools": ["terraform", "bash", "kubectl", "bash"],
        },
        "metadata": {"domains": ["coding"], "tags": ["aws"]},
    },
    "claude": {
        "enabled": True,
        "mode": "acp",
        "description": "Claude Code agent",
        "capabilities": {"tools": "not-a-list", "languages": {"python": True}},
    },
    "opengame": {"enabled": True, "mode": "acp", "description": "Game generator"},
    "disabled-one": {"enabled": False, "mode": "acp", "description": "off"},
    "burst": {"enabled": True, "mode": "acp", "pool": "lambda", "description": "lambda"},
    "root": {"enabled": True, "mode": "acp", "trust": "unrestricted", "description": "root"},
    "trae": {"enabled": True, "mode": "pty", "description": "Trae"},
    "auto": {"enabled": True, "description": "should never be a candidate"},
    "_public_workdir": "/tmp/acp-public",  # main.py injects a non-dict entry; must be tolerated
}


def _jev_response(choice, probabilities, confidence, model="jev-1.13.0", tokens=1000):
    return {
        "model": model,
        "answers": {
            "agent": {
                "type": "choice",
                "choice": choice,
                "probabilities": probabilities,
                "confidence": confidence,
            }
        },
        "usage": {"input_tokens": tokens, "output_tokens": 100},
    }


def _router(handler, **kw):
    """Router wired to a MockTransport. `handler(request) -> httpx.Response`."""
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="https://api.typesafe.ai",
        headers={"Authorization": f"Bearer {KEY}"},
    )
    kw.setdefault("default_agent", "kiro")
    return JevRouter(KEY, AGENTS, http_client=client, **kw)


# --------------------------------------------------------------------- criteria
def test_criteria_filters_and_describes_agents():
    crit = JevRouter.build_criteria(AGENTS)
    # eligible: enabled, local, not router itself, not unrestricted, dict entries only
    assert set(crit) == {"kiro", "claude", "opengame", "trae", OTHER}
    assert crit["kiro"] == {
        "what": "Kiro CLI agent",
        "domains": ["cloud", "coding", "devops"],
        "tags": ["aws", "cli-first"],
        "tools": ["bash", "kubectl", "terraform"],  # deduped + sorted
    }
    # malformed tools (not a list) and non-domain capability keys are ignored
    assert crit["claude"] == {"what": "Claude Code agent"}
    assert isinstance(crit[OTHER], str)


def test_criteria_allowlist_and_denylist():
    assert set(JevRouter.build_criteria(AGENTS, exclude=["trae"])) == {
        "kiro",
        "claude",
        "opengame",
        OTHER,
    }
    # candidates allowlist wins, and explicitly naming an unrestricted agent admits it
    assert set(JevRouter.build_criteria(AGENTS, candidates=["claude", "root"])) == {
        "claude",
        "root",
        OTHER,
    }
    # a custom router name is excluded from its own options
    crit = JevRouter.build_criteria({**AGENTS, "smart": {"enabled": True}}, agent_name="smart")
    assert "smart" not in crit and "auto" in crit


def test_constructor_validation():
    with pytest.raises(ValueError):
        JevRouter("", AGENTS, default_agent="kiro")
    with pytest.raises(ValueError):
        JevRouter(KEY, AGENTS, default_agent="kiro", base_url="http://api.typesafe.ai")
    with pytest.raises(ValueError):
        JevRouter(KEY, AGENTS, default_agent="nope")
    with pytest.raises(ValueError):
        JevRouter(KEY, AGENTS, default_agent="kiro", confidence_threshold=1.5)
    with pytest.raises(ValueError):
        JevRouter(KEY, AGENTS, default_agent="kiro", candidates=["disabled-one"])


def test_repr_and_status_never_contain_key():
    r = _router(lambda req: httpx.Response(200, json=_jev_response("kiro", {"kiro": 1.0}, 1.0)))
    assert KEY not in repr(r)
    assert KEY not in json.dumps(r.status())
    assert r.status()["candidates"] == ["kiro", "claude", "opengame", "trae"]
    assert r.status()["enabled"] is True


# ----------------------------------------------------------------------- decide
@pytest.mark.asyncio
async def test_decide_routes_when_confident_and_sends_bearer():
    seen = {}

    def handler(req: httpx.Request):
        seen["auth"] = req.headers.get("authorization")
        seen["path"] = req.url.path
        seen["body"] = json.loads(req.content)
        return httpx.Response(
            200, json=_jev_response("opengame", {"opengame": 0.9, "kiro": 0.1}, 0.85, tokens=1234)
        )

    r = _router(handler)
    d = await r.decide("make a dinosaur game")
    assert seen["auth"] == f"Bearer {KEY}"
    assert seen["path"] == "/v1/systemone"
    q = seen["body"]["questions"]["agent"]
    assert q["type"] == "choice" and set(q["criteria"]) == {
        "kiro",
        "claude",
        "opengame",
        "trae",
        OTHER,
    }
    assert seen["body"]["state"] == {"task": "make a dinosaur game"}
    assert seen["body"]["model"] == "jev-latest"

    assert d.agent == "opengame" and d.fallback is False and d.reason == "jev"
    assert d.jev_choice == "opengame" and d.confidence == 0.85
    assert d.probabilities == {"opengame": 0.9, "kiro": 0.1}
    assert d.model == "jev-1.13.0" and d.input_tokens == 1234
    assert KEY not in json.dumps(d.to_dict())
    assert (
        r.stats["decisions"] == 1
        and r.stats["routed"] == 1
        and r.stats["by_agent"] == {"opengame": 1}
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "choice,conf,expected_reason",
    [
        ("claude", 0.3, "low_confidence"),  # below default 0.5 threshold
        (OTHER, 0.95, "other"),
        ("root", 0.99, "invalid_choice"),  # not a candidate we offered
        ("nonexistent", 0.99, "invalid_choice"),
    ],
)
async def test_decide_falls_back_to_default(choice, conf, expected_reason):
    r = _router(lambda req: httpx.Response(200, json=_jev_response(choice, {choice: conf}, conf)))
    d = await r.decide("something")
    assert d.agent == "kiro" and d.fallback is True and d.reason == expected_reason
    assert d.jev_choice == choice  # raw answer still surfaced for observability
    assert r.stats["fallback"] == 1 and r.stats["jev_errors"] == 0


@pytest.mark.asyncio
async def test_threshold_is_configurable():
    r = _router(
        lambda req: httpx.Response(200, json=_jev_response("claude", {"claude": 0.6}, 0.55)),
        confidence_threshold=0.5,
    )
    assert (await r.decide("x")).agent == "claude"
    r2 = _router(
        lambda req: httpx.Response(200, json=_jev_response("claude", {"claude": 0.6}, 0.55)),
        confidence_threshold=0.9,
    )
    assert (await r2.decide("x")).reason == "low_confidence"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "make_response,expected_reason",
    [
        (lambda: httpx.Response(429, json={"error": "rate limited"}), "jev_error:rate_limit"),
        (lambda: httpx.Response(500, text="boom"), "jev_error:http_500"),
        (lambda: httpx.Response(401, json={"error": "bad key"}), "jev_error:http_401"),
        (lambda: httpx.Response(200, text="not json"), "jev_error:bad_response"),
        (lambda: httpx.Response(200, json={"answers": {}}), "jev_error:bad_response"),
    ],
)
async def test_decide_degrades_on_api_errors(make_response, expected_reason):
    r = _router(lambda req: make_response())
    d = await r.decide("task")
    assert d.agent == "kiro" and d.fallback is True and d.reason == expected_reason
    assert d.jev_choice == "" and KEY not in d.error
    assert r.stats["jev_errors"] == 1


@pytest.mark.asyncio
async def test_decide_degrades_on_timeout_and_connection_error():
    def timeout(req):
        raise httpx.ReadTimeout("slow", request=req)

    def conn(req):
        raise httpx.ConnectError("refused", request=req)

    d1 = await _router(timeout).decide("t")
    assert d1.reason == "jev_error:timeout" and d1.agent == "kiro"
    d2 = await _router(conn).decide("t")
    assert d2.reason == "jev_error:connection" and d2.error == "ConnectError"


@pytest.mark.asyncio
async def test_prompt_is_truncated_before_leaving_the_host():
    seen = {}

    def handler(req):
        seen["state"] = json.loads(req.content)["state"]["task"]
        return httpx.Response(200, json=_jev_response("kiro", {"kiro": 1.0}, 1.0))

    r = _router(handler, max_state_chars=50)
    await r.decide("A" * 500)
    assert len(seen["state"]) == 50


# ---------------------------------------------------------------------- handler
class _FakeAgent:
    def __init__(self, name):
        self.name = name
        self.calls = []

    async def run(self, input, context):
        self.calls.append((input, context))
        yield MessagePart(content=f"hello from {self.name}", content_type="text/plain")


def _collect_text(items):
    out = []
    for it in items:
        if isinstance(it, Message):
            out += [p.content for p in it.parts]
        elif isinstance(it, MessagePart):
            out.append(it.content)
    return out


@pytest.mark.asyncio
async def test_handler_announces_route_then_delegates_unchanged():
    r = _router(lambda req: httpx.Response(200, json=_jev_response("claude", {"claude": 0.9}, 0.9)))
    registry = {"kiro": _FakeAgent("kiro"), "claude": _FakeAgent("claude")}
    handler = make_router_agent_handler(r, lambda: registry)
    msgs = [Message(parts=[MessagePart(content="refactor this", content_type="text/plain")])]
    ctx = object()

    items = [x async for x in handler(msgs, ctx)]
    texts = _collect_text(items)
    assert len(texts) == 2
    assert texts[0].startswith("🧭 Route: auto → claude")
    meta = json.loads(texts[0].split("<!-- ")[1].rstrip(" -->"))
    assert meta["agent"] == "claude" and meta["reason"] == "jev" and meta["router"] == "auto"
    assert KEY not in texts[0]
    assert texts[1] == "hello from claude"
    # the chosen agent received the *original* input and context objects
    assert registry["claude"].calls == [(msgs, ctx)]
    assert registry["kiro"].calls == []
    assert isinstance(items[0], Message) and items[0].parts[0].name == "route_info"


@pytest.mark.asyncio
async def test_handler_uses_default_when_chosen_agent_missing_from_registry():
    r = _router(
        lambda req: httpx.Response(200, json=_jev_response("opengame", {"opengame": 0.9}, 0.9))
    )
    registry = {"kiro": _FakeAgent("kiro")}  # opengame configured but not live
    handler = make_router_agent_handler(r, lambda: registry)
    msgs = [Message(parts=[MessagePart(content="game", content_type="text/plain")])]
    texts = _collect_text([x async for x in handler(msgs, None)])
    assert "auto → kiro" in texts[0] and "invalid_choice" in texts[0]
    assert texts[1] == "hello from kiro"


@pytest.mark.asyncio
async def test_handler_errors_cleanly_when_default_missing():
    r = _router(lambda req: httpx.Response(500))
    handler = make_router_agent_handler(r, lambda: {})
    msgs = [Message(parts=[MessagePart(content="x", content_type="text/plain")])]
    texts = _collect_text([x async for x in handler(msgs, None)])
    assert texts[0].startswith("🧭 Route: auto → kiro")
    assert texts[1].startswith("[error] router: default agent 'kiro' is not registered")


# ----------------------------------------------------------------------- routes
def _app(router):
    app = FastAPI()
    router_routes.register(app, router)
    return app


@pytest.mark.asyncio
async def test_routes_disabled_return_503():
    app = _app(None)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        assert (await c.get("/route/status")).status_code == 503
        assert (await c.post("/route/preview", json={"prompt": "x"})).status_code == 503


@pytest.mark.asyncio
async def test_route_preview_and_status():
    r = _router(lambda req: httpx.Response(200, json=_jev_response("claude", {"claude": 0.8}, 0.7)))
    app = _app(r)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        resp = await c.post("/route/preview", json={"prompt": "refactor"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["agent"] == "claude" and body["reason"] == "jev" and body["router"] == "auto"
        assert KEY not in resp.text

        assert (await c.post("/route/preview", json={"prompt": ""})).status_code == 400
        assert (await c.post("/route/preview", json={})).status_code == 400
        assert (await c.post("/route/preview", content=b"nope")).status_code == 400
        assert (await c.post("/route/preview", json={"prompt": "A" * 20001})).status_code == 413

        st = await c.get("/route/status")
        assert st.status_code == 200
        assert st.json()["stats"]["decisions"] == 1
        assert KEY not in st.text


def test_route_decision_to_dict_is_json_safe():
    d = RouteDecision(
        "kiro", "jev", False, jev_choice="kiro", confidence=0.9, probabilities={"kiro": 0.9}
    )
    json.dumps(d.to_dict())


# ------------------------------------------------------------ jobs integration
class _FakeConn:
    def __init__(self, text):
        self._text = text

    async def session_prompt(self, prompt):
        yield {"params": {"kind": "text", "data": {"content": self._text}}}
        yield {"_prompt_result": {"result": {"stopReason": "end"}}}


class _FakePool:
    def __init__(self, agents):
        self._config = {a: {} for a in agents}
        self._connections = {}
        self.calls = []

    async def get_or_create(self, agent, session_id, cwd="", profile=None):
        self.calls.append(agent)
        return _FakeConn(f"hello from {agent}")

    async def remove(self, agent, session_id):
        pass


def _job_manager(pool, router):
    import tempfile
    from types import SimpleNamespace
    from unittest.mock import MagicMock

    from src.jobs import JobManager
    from src.store import JobStore

    mgr = JobManager.__new__(JobManager)
    mgr._pool = pool
    mgr._pty_configs = {}
    mgr._jobs = {}
    mgr._stats = None
    mgr._webhook_url = ""
    mgr._webhook_format = "openclaw"
    mgr._base_url = ""
    mgr._sender = MagicMock()
    mgr._store = JobStore(os.path.join(tempfile.mkdtemp(), "t.db"))
    mgr._pending_recovery = []
    mgr._allowed_private_targets = frozenset()
    mgr._app = SimpleNamespace(state=SimpleNamespace(jev_router=router, acp_agents={}))
    return mgr


def _job(agent, prompt="do it"):
    from src.jobs import Job

    return Job(job_id="j1", agent=agent, session_id="s1", prompt=prompt, cwd="")


@pytest.mark.asyncio
async def test_jobs_resolve_auto_to_concrete_agent_and_prefix_route_info():
    """/jobs {agent_name: auto}: JobManager asks Jev, rewrites job.agent, runs via the pool."""
    r = _router(lambda req: httpx.Response(200, json=_jev_response("claude", {"claude": 0.9}, 0.9)))
    pool = _FakePool(["kiro", "claude"])
    mgr = _job_manager(pool, r)
    job = _job("auto", "refactor this module")

    await mgr._run(job)

    assert job.status == "completed"
    assert job.agent == "claude" and pool.calls == ["claude"]
    assert job.result.startswith("🧭 Route: auto → claude (jev: claude, confidence 0.90, jev)\n")
    assert job.result.endswith("hello from claude")
    assert KEY not in job.result
    # the concrete agent is what fallback bookkeeping sees — not the virtual name
    assert job.original_agent == "claude" and job.fallback_history == []


@pytest.mark.asyncio
async def test_jobs_auto_uses_default_when_pick_not_dispatchable():
    r = _router(
        lambda req: httpx.Response(200, json=_jev_response("opengame", {"opengame": 0.9}, 0.9))
    )
    pool = _FakePool(["kiro"])  # opengame configured for Jev but not runnable here
    mgr = _job_manager(pool, r)
    job = _job("auto")

    await mgr._run(job)

    assert job.status == "completed" and job.agent == "kiro" and pool.calls == ["kiro"]
    assert "auto → kiro" in job.result and "invalid_choice" in job.result


@pytest.mark.asyncio
async def test_jobs_non_router_agent_untouched_and_router_disabled_is_noop():
    r = _router(lambda req: httpx.Response(500))
    pool = _FakePool(["kiro"])
    mgr = _job_manager(pool, r)
    job = _job("kiro")
    await mgr._run(job)
    assert job.agent == "kiro" and job.result == "hello from kiro"  # no route_info
    assert r.stats["decisions"] == 0

    mgr2 = _job_manager(_FakePool(["kiro"]), None)  # router disabled: app.state.jev_router None
    job2 = _job("kiro")
    await mgr2._run(job2)
    assert job2.status == "completed" and job2.result == "hello from kiro"
