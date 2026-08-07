"""HTTP-level tests for POST /jobs (src/routes/jobs.py) — SSRF guard on callback_url.

No test previously exercised this route (or src/routes/mesh.py, see
test_mesh_routes.py) as a real HTTP request; the SSRF fix's unit tests only
covered JobManager.submit() directly. These prove the route wiring itself —
the try/except UnsafeUrlError -> 400 — actually behaves correctly.
"""

import asyncio
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import httpx
import pytest
from fastapi import FastAPI

from src.jobs import JobManager
from src.routes import jobs as jobs_routes


class _FakeConn:
    async def session_prompt(self, prompt):
        yield {"params": {"kind": "text", "data": {"content": "ok"}}}
        yield {"_prompt_result": {"result": {"stopReason": "end"}}}


class _FakePool:
    _connections = {}

    async def get_or_create(self, agent, session_id, cwd="", profile=None):
        return _FakeConn()

    async def remove(self, agent, session_id):
        pass


def _app(allowed_private_targets=frozenset()):
    db_path = os.path.join(tempfile.mkdtemp(), "test.db")
    job_mgr = JobManager(
        pool=_FakePool(), db_path=db_path, allowed_private_targets=allowed_private_targets
    )
    app = FastAPI()
    jobs_routes.register(app, job_mgr, webhook_account_id="", webhook_default_target="")
    return app, job_mgr


@pytest.mark.asyncio
async def test_post_jobs_rejects_unsafe_callback_url():
    app, job_mgr = _app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/jobs",
            json={
                "agent_name": "kiro",
                "prompt": "hi",
                "callback_url": "http://127.0.0.1:9999/hook",
            },
        )
    assert resp.status_code == 400
    assert "unsafe callback_url" in resp.json()["error"]
    assert job_mgr._jobs == {}  # nothing was queued


@pytest.mark.asyncio
async def test_post_jobs_allows_safe_callback_url():
    app, _job_mgr = _app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/jobs",
            json={
                "agent_name": "kiro",
                "prompt": "hi",
                "callback_url": "https://example.com/hook",
            },
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "pending"
    assert "job_id" in body
    await asyncio.sleep(0.05)  # let the background task settle before teardown


@pytest.mark.asyncio
async def test_post_jobs_without_callback_url_is_unaffected():
    """No callback_url at all — the SSRF guard must not interfere with a normal submit."""
    app, _job_mgr = _app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/jobs", json={"agent_name": "kiro", "prompt": "hi"})
    assert resp.status_code == 200
    await asyncio.sleep(0.05)


@pytest.mark.asyncio
async def test_post_jobs_allows_private_callback_url_when_opted_in():
    app, _job_mgr = _app(allowed_private_targets=frozenset({"127.0.0.1"}))
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/jobs",
            json={
                "agent_name": "kiro",
                "prompt": "hi",
                "callback_url": "http://127.0.0.1:9999/hook",
            },
        )
    assert resp.status_code == 200
    await asyncio.sleep(0.05)


@pytest.mark.asyncio
async def test_post_jobs_allowed_private_targets_never_cover_metadata():
    """allowed_private_targets is scoped to the private-range check only —
    a broad allowlisted CIDR must not also unblock cloud metadata targets."""
    app, job_mgr = _app(allowed_private_targets=frozenset({"0.0.0.0/0"}))
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/jobs",
            json={
                "agent_name": "kiro",
                "prompt": "hi",
                "callback_url": "http://169.254.169.254/latest/meta-data/",
            },
        )
    assert resp.status_code == 400
    assert job_mgr._jobs == {}
