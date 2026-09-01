"""Tests for src/acp_patch.py — SDK hotfixes for the shared-Event busy loop
(2026-08-11 "health unreachable" incident) and streaming circuit-breaker gating."""

import asyncio
from datetime import timedelta

import pytest
from acp_sdk.server.store.store import StoreModel

from src.acp_patch import (
    PerKeyEventMemoryStore,
    _apply_uvicorn_loop_shim,
    apply_executor_patch,
)
from src.circuit_breaker import (
    CircuitBreaker,
    CircuitBreakerConfig,
    CircuitBreakerOpenError,
    CircuitState,
)


class Doc(StoreModel):
    n: int = 0


def make_store():
    return PerKeyEventMemoryStore(limit=100, ttl=timedelta(hours=1))


# ---------------------------------------------------------------------------
# PerKeyEventMemoryStore
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unrelated_write_does_not_wake_watcher():
    store = make_store()
    wakeups = 0

    async def watcher():
        nonlocal wakeups
        async for _ in store.watch("mine"):
            wakeups += 1

    t = asyncio.create_task(watcher())
    await asyncio.sleep(0.02)
    for i in range(10):
        await store.set("other", Doc(n=i))
    await asyncio.sleep(0.05)
    t.cancel()
    assert wakeups == 0


@pytest.mark.asyncio
async def test_own_key_write_wakes_watcher():
    store = make_store()
    got = []

    async def watcher():
        async for d in store.watch("k"):
            got.append(d.n)
            if d.n >= 1:
                break

    t = asyncio.create_task(watcher())
    await asyncio.sleep(0.02)
    await store.set("k", Doc(n=0))
    await asyncio.sleep(0.02)
    await store.set("k", Doc(n=1))
    await asyncio.wait_for(t, timeout=2)
    assert got == [0, 1]


@pytest.mark.asyncio
async def test_multiple_watchers_same_key_all_wake():
    store = make_store()
    got = [[], []]

    async def watcher(i):
        async for d in store.watch("k"):
            got[i].append(d.n)
            break

    ts = [asyncio.create_task(watcher(i)) for i in range(2)]
    await asyncio.sleep(0.02)
    await store.set("k", Doc(n=7))
    await asyncio.wait_for(asyncio.gather(*ts), timeout=2)
    assert got == [[7], [7]]


@pytest.mark.asyncio
async def test_ready_event_is_set():
    store = make_store()
    ready = asyncio.Event()

    async def watcher():
        async for _ in store.watch("k", ready=ready):
            break

    t = asyncio.create_task(watcher())
    await asyncio.wait_for(ready.wait(), timeout=2)
    t.cancel()


@pytest.mark.asyncio
async def test_key_event_garbage_collected_after_watcher_exits():
    store = make_store()

    async def watcher():
        async for _ in store.watch("gone"):
            break

    t = asyncio.create_task(watcher())
    await asyncio.sleep(0.02)
    assert "gone" in store._key_events
    await store.set("gone", Doc())
    await asyncio.wait_for(t, timeout=2)
    del t
    import gc

    # async generator finalization is scheduled on the loop; give it a tick
    for _ in range(5):
        gc.collect()
        await asyncio.sleep(0.01)
        if "gone" not in store._key_events:
            break
    assert "gone" not in store._key_events


# ---------------------------------------------------------------------------
# Executor cancellation-watcher reaping
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_executor_patch_reaps_watcher_on_run_completion():
    from acp_sdk.server.executor import Executor

    apply_executor_patch()

    store = make_store()
    ex = Executor.__new__(Executor)
    ex.cancel_store = store
    ex.executor = None
    ex.run_data = type("RD", (), {"key": "run-x"})()

    async def fake_execute(input=None, executor=None, wait=None):
        await asyncio.sleep(0.05)

    ex._execute = fake_execute
    Executor.execute(ex, [], wait=asyncio.Event())
    await asyncio.sleep(0.02)
    assert not ex.watcher.done()
    await asyncio.sleep(0.15)
    assert ex.watcher.done()


def test_executor_patch_is_idempotent():
    from acp_sdk.server.executor import Executor

    apply_executor_patch()
    first = Executor.execute
    apply_executor_patch()
    assert Executor.execute is first


# ---------------------------------------------------------------------------
# uvicorn.config.LoopSetupType compat shim (issue #21)
# ---------------------------------------------------------------------------


def test_uvicorn_loop_shim_makes_server_run_hints_resolvable():
    """acp-sdk 1.0.3 annotates Server.run's `loop` as uvicorn.config.LoopSetupType,
    renamed to LoopFactoryType in uvicorn 0.36. The shim (applied at acp_patch
    import time) must let get_type_hints(Server.run) resolve without AttributeError."""
    import typing

    import acp_sdk.server as acp_server

    # Importing src.acp_patch (done at module load) has already run the shim.
    hints = typing.get_type_hints(acp_server.Server.run)
    # `loop` resolves to the Literal that LoopFactoryType/LoopSetupType both name.
    assert "loop" in hints


def test_uvicorn_loop_shim_aliases_or_noops():
    """On uvicorn >= 0.36 the shim aliases LoopSetupType -> LoopFactoryType;
    on older uvicorn LoopSetupType already exists so it's a no-op. Either way
    the name is present and equal to LoopFactoryType when that exists."""
    import uvicorn.config as uc

    _apply_uvicorn_loop_shim()  # idempotent
    assert hasattr(uc, "LoopSetupType")
    if hasattr(uc, "LoopFactoryType"):
        assert uc.LoopSetupType is uc.LoopFactoryType


def test_uvicorn_loop_shim_is_idempotent():
    import uvicorn.config as uc

    _apply_uvicorn_loop_shim()
    first = uc.LoopSetupType
    _apply_uvicorn_loop_shim()
    assert uc.LoopSetupType is first


# ---------------------------------------------------------------------------
# Streaming circuit-breaker gate (before_call/on_success/on_failure)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_manual_gate_success_and_failure_accounting():
    cb = CircuitBreaker(
        "t", CircuitBreakerConfig(failure_threshold=2, expected_exceptions=(ValueError,))
    )

    async def gen(fail):
        yield "a"
        if fail:
            raise ValueError("boom")

    async def gated(fail):
        await cb.before_call()
        try:
            async for p in gen(fail):
                yield p
        except cb.config.expected_exceptions:
            await cb.on_failure()
            raise
        else:
            await cb.on_success()

    async for _ in gated(False):
        pass
    assert cb.success_calls == 1

    for _ in range(2):
        with pytest.raises(ValueError):
            async for _ in gated(True):
                pass
    assert cb.failure_calls == 2
    assert cb.state == CircuitState.OPEN
    with pytest.raises(CircuitBreakerOpenError):
        await cb.before_call()


@pytest.mark.asyncio
async def test_call_still_works_via_public_gate():
    """CircuitBreaker.call refactored onto before_call/on_success/on_failure —
    behavior must be unchanged."""
    cb = CircuitBreaker(
        "t", CircuitBreakerConfig(failure_threshold=1, expected_exceptions=(ValueError,))
    )

    async def ok():
        return 42

    assert await cb.call(ok) == 42
    assert cb.success_calls == 1

    async def bad():
        raise ValueError("x")

    with pytest.raises(ValueError):
        await cb.call(bad)
    assert cb.state == CircuitState.OPEN
    with pytest.raises(CircuitBreakerOpenError):
        await cb.call(ok)
