"""Tests for AcpProcessPool.get_or_create bounded wait (v0.40.0)."""

import asyncio
import time

import pytest

from src.acp_client import AcpProcessPool, PoolExhaustedError


def make_pool(timeout: float, agents: dict | None = None) -> AcpProcessPool:
    pool = AcpProcessPool(
        agents_config=agents or {"a": {}, "b": {}}, max_processes=2, max_per_agent=1
    )
    pool._acquire_timeout = timeout
    return pool


@pytest.mark.asyncio
async def test_wait_then_succeed(monkeypatch):
    """Pool full on first attempts → waits → slot frees → returns connection."""
    pool = make_pool(timeout=10)
    calls = {"n": 0}

    async def fake(agent, session_id, cwd="", profile=None, resume_session_id=""):
        calls["n"] += 1
        if calls["n"] < 3:
            raise PoolExhaustedError("per-agent limit")
        return f"conn-{agent}"

    monkeypatch.setattr(pool, "_get_or_create_unlocked", fake)
    t0 = time.monotonic()
    conn = await pool.get_or_create("a", "s1")
    dt = time.monotonic() - t0
    assert conn == "conn-a"
    assert calls["n"] == 3
    assert 3.0 < dt < 7.0  # two 2s sleeps between three attempts


@pytest.mark.asyncio
async def test_timeout_raises(monkeypatch):
    """Pool full for longer than acquire_timeout → PoolExhaustedError, bounded."""
    pool = make_pool(timeout=5)

    async def always_full(agent, session_id, cwd="", profile=None, resume_session_id=""):
        raise PoolExhaustedError("global limit")

    monkeypatch.setattr(pool, "_get_or_create_unlocked", always_full)
    t0 = time.monotonic()
    with pytest.raises(PoolExhaustedError):
        await pool.get_or_create("a", "s1")
    dt = time.monotonic() - t0
    assert dt <= 5.5  # never exceeds timeout
    assert dt >= 3.0  # actually waited before giving up


@pytest.mark.asyncio
async def test_zero_timeout_fail_fast(monkeypatch):
    """acquire_timeout=0 → old behavior: single attempt, immediate raise."""
    pool = make_pool(timeout=0)
    calls = {"n": 0}

    async def always_full(agent, session_id, cwd="", profile=None, resume_session_id=""):
        calls["n"] += 1
        raise PoolExhaustedError("global limit")

    monkeypatch.setattr(pool, "_get_or_create_unlocked", always_full)
    t0 = time.monotonic()
    with pytest.raises(PoolExhaustedError):
        await pool.get_or_create("a", "s1")
    assert calls["n"] == 1
    assert time.monotonic() - t0 < 0.5


@pytest.mark.asyncio
async def test_wait_does_not_block_other_agents(monkeypatch):
    """The wait loop must not hold the pool lock — agent b acquires
    while agent a is waiting for a slot."""
    pool = make_pool(timeout=10)

    async def unlocked(agent, session_id, cwd="", profile=None, resume_session_id=""):
        if agent == "a":
            raise PoolExhaustedError("per-agent limit for a")
        return f"conn-{agent}"

    monkeypatch.setattr(pool, "_get_or_create_unlocked", unlocked)

    waiter = asyncio.create_task(pool.get_or_create("a", "s1"))
    await asyncio.sleep(0.3)  # waiter is now sleeping between attempts
    t0 = time.monotonic()
    conn = await pool.get_or_create("b", "s2")
    dt = time.monotonic() - t0
    assert conn == "conn-b"
    assert dt < 0.5, f"agent b blocked for {dt:.1f}s while agent a waits"
    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter


@pytest.mark.asyncio
async def test_other_errors_propagate_immediately(monkeypatch):
    """Non-pool errors (e.g. AcpError) are not retried by the wait loop."""
    from src.acp_client import AcpError

    pool = make_pool(timeout=10)
    calls = {"n": 0}

    async def broken(agent, session_id, cwd="", profile=None, resume_session_id=""):
        calls["n"] += 1
        raise AcpError("agent not found: a")

    monkeypatch.setattr(pool, "_get_or_create_unlocked", broken)
    t0 = time.monotonic()
    with pytest.raises(AcpError):
        await pool.get_or_create("a", "s1")
    assert calls["n"] == 1
    assert time.monotonic() - t0 < 0.5
