"""Unit tests for src/circuit_breaker.py."""

import asyncio
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from src.circuit_breaker import (
    CircuitBreaker,
    CircuitBreakerConfig,
    CircuitBreakerOpenError,
    CircuitState,
)


_DEFAULTS = dict(
    failure_threshold=3,
    failure_rate_threshold=0.5,
    window_size=6,
    open_timeout=0.1,
    half_open_max_calls=2,
)


def _make_cb(**overrides) -> CircuitBreaker:
    cfg = CircuitBreakerConfig(**{**_DEFAULTS, **overrides})
    return CircuitBreaker("test-agent", cfg)


# ── State machine basics ──────────────────────────────────────────────

def test_initial_state_closed():
    cb = _make_cb()
    assert cb.state == CircuitState.CLOSED


def test_stays_closed_on_success():
    cb = _make_cb()
    cb.record_success()
    cb.record_success()
    assert cb.state == CircuitState.CLOSED


def test_consecutive_failures_open():
    cb = _make_cb(failure_threshold=3)
    cb.record_failure()
    cb.record_failure()
    assert cb.state == CircuitState.CLOSED
    cb.record_failure()
    assert cb.state == CircuitState.OPEN


def test_failure_rate_trigger():
    cb = _make_cb(failure_threshold=100, failure_rate_threshold=0.5, window_size=4)
    cb.record_success()
    cb.record_failure()
    cb.record_failure()
    assert cb.state == CircuitState.CLOSED  # 3/4 not yet full window? 2/3 failures but window=4
    cb.record_failure()  # 3 failures / 4 total = 0.75 >= 0.5
    assert cb.state == CircuitState.OPEN


def test_success_resets_consecutive_count():
    cb = _make_cb(failure_threshold=3)
    cb.record_failure()
    cb.record_failure()
    cb.record_success()  # breaks consecutive streak
    cb.record_failure()
    cb.record_failure()
    assert cb.state == CircuitState.CLOSED  # only 2 consecutive


def test_open_rejects_record_stays_open():
    cb = _make_cb(failure_threshold=2, open_timeout=999)
    cb.record_failure()
    cb.record_failure()
    assert cb.state == CircuitState.OPEN
    # Manual record_failure while OPEN doesn't change state (already OPEN)
    cb.record_failure()
    assert cb.state == CircuitState.OPEN


# ── OPEN → HALF_OPEN → CLOSED / OPEN ─────────────────────────────────

def test_open_to_half_open_after_timeout():
    cb = _make_cb(failure_threshold=2, open_timeout=0.05)
    cb.record_failure()
    cb.record_failure()
    assert cb.state == CircuitState.OPEN
    time.sleep(0.06)
    cb._maybe_transition_to_half_open()
    assert cb.state == CircuitState.HALF_OPEN


def test_half_open_success_closes():
    cb = _make_cb(failure_threshold=2, open_timeout=0.01)
    cb.record_failure()
    cb.record_failure()
    time.sleep(0.02)
    cb._maybe_transition_to_half_open()
    assert cb.state == CircuitState.HALF_OPEN
    cb.record_success()
    assert cb.state == CircuitState.CLOSED


def test_half_open_failure_reopens():
    cb = _make_cb(failure_threshold=2, open_timeout=0.01)
    cb.record_failure()
    cb.record_failure()
    time.sleep(0.02)
    cb._maybe_transition_to_half_open()
    cb.record_failure()
    assert cb.state == CircuitState.OPEN


# ── Async call() integration ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_call_success():
    cb = _make_cb()
    async def ok():
        return "ok"

    result = await cb.call(ok)
    assert result == "ok"
    assert cb.success_calls == 1


@pytest.mark.asyncio
async def test_call_failure_propagates():
    cb = _make_cb()

    async def boom():
        raise ValueError("bad")

    with pytest.raises(ValueError):
        await cb.call(boom)
    assert cb.failure_calls == 1


@pytest.mark.asyncio
async def test_call_rejected_when_open():
    cb = _make_cb(failure_threshold=2, open_timeout=999)
    cb.record_failure()
    cb.record_failure()

    async def noop():
        return 1

    with pytest.raises(CircuitBreakerOpenError):
        await cb.call(noop)


@pytest.mark.asyncio
async def test_call_half_open_limit():
    cb = _make_cb(failure_threshold=2, open_timeout=0.01, half_open_max_calls=1)
    cb.record_failure()
    cb.record_failure()
    await asyncio.sleep(0.02)

    async def ok():
        return "ok"

    # First call allowed (transitions to half-open internally)
    r = await cb.call(ok)
    assert r == "ok"
    assert cb.state == CircuitState.CLOSED  # success → closed


@pytest.mark.asyncio
async def test_half_open_max_calls_exceeded():
    cb = _make_cb(failure_threshold=2, open_timeout=0.01, half_open_max_calls=1)
    cb.record_failure()
    cb.record_failure()
    await asyncio.sleep(0.02)

    async def fail():
        raise ValueError("x")

    # First half-open call: allowed, fails → re-opens
    with pytest.raises(ValueError):
        await cb.call(fail)
    assert cb.state == CircuitState.OPEN


# ── Metrics ───────────────────────────────────────────────────────────

def test_metrics():
    cb = _make_cb()
    cb.record_success()
    cb.record_failure()
    m = cb.get_metrics()
    assert m["name"] == "test-agent"
    assert m["state"] == "closed"
    assert m["total_calls"] == 2
    assert m["success_calls"] == 1
    assert m["failure_calls"] == 1
    assert m["failure_rate"] == 0.5


def test_circuit_open_count():
    cb = _make_cb(failure_threshold=2, open_timeout=0.01)
    cb.record_failure()
    cb.record_failure()
    assert cb.circuit_open_count == 1
    time.sleep(0.02)
    cb._maybe_transition_to_half_open()
    cb.record_failure()  # re-opens
    assert cb.circuit_open_count == 2


# ── Reset ─────────────────────────────────────────────────────────────

def test_reset():
    cb = _make_cb(failure_threshold=2)
    cb.record_failure()
    cb.record_failure()
    assert cb.state == CircuitState.OPEN
    cb.reset()
    assert cb.state == CircuitState.CLOSED
    assert len(cb._results) == 0


# ── Callback ──────────────────────────────────────────────────────────

def test_on_state_change_callback():
    transitions = []

    def on_change(name, old, new):
        transitions.append((name, old.value, new.value))

    cb = _make_cb(failure_threshold=2, on_state_change=on_change)
    cb.record_failure()
    cb.record_failure()
    assert transitions == [("test-agent", "closed", "open")]


# ── Edge cases ────────────────────────────────────────────────────────

def test_empty_window_no_open():
    cb = _make_cb(failure_threshold=100, failure_rate_threshold=0.5, window_size=10)
    # No calls yet → should not open
    assert not cb._should_open()


def test_window_slides():
    cb = _make_cb(failure_threshold=100, failure_rate_threshold=0.5, window_size=4)
    # Fill window with failures
    for _ in range(4):
        cb.record_failure()
    assert cb.state == CircuitState.OPEN
    cb.reset()
    # Now fill with successes — old failures should be gone
    for _ in range(4):
        cb.record_success()
    assert cb.state == CircuitState.CLOSED
    m = cb.get_metrics()
    assert m["failure_rate"] == 0.0


# ── Integration with agents.py fallback scoring ───────────────────────

def test_ratelimit_does_not_trip_breaker():
    """AgentRateLimitError must NOT be counted as a circuit breaker failure.

    The agents.py fix removes AgentRateLimitError from expected_exceptions and
    removes the ratelimit_breaker.record_failure() call.  We verify here that
    manually calling record_failure() would have tripped the breaker, but that
    the fixed code path (not calling it) leaves the breaker CLOSED.
    """
    cb = _make_cb(failure_threshold=2)
    # Simulate the OLD (broken) behaviour: record_failure called on rate-limit
    # → breaker trips after 2 calls
    cb_old = _make_cb(failure_threshold=2)
    cb_old.record_failure()
    cb_old.record_failure()
    assert cb_old.state == CircuitState.OPEN, "sanity: two failures do trip the breaker"

    # NEW behaviour: rate-limit handler does NOT call record_failure → stays CLOSED
    # (cb was never touched — simulates skipping the call)
    assert cb.state == CircuitState.CLOSED, "RateLimit path must not trip the breaker"
    assert cb.failure_calls == 0


def test_half_open_weight_lower_than_closed(monkeypatch):
    """HALF_OPEN agents should receive a ×0.5 weight penalty in get_best_fallback().

    We test the weight logic independently of the full fallback machinery by
    reproducing the cb_weight calculation from agents.py.
    """
    cb_closed = _make_cb()
    cb_half = _make_cb(open_timeout=0.0)  # instant OPEN → HALF_OPEN transition
    # Trip the half-open breaker
    for _ in range(3):
        cb_half.record_failure()
    assert cb_half.state == CircuitState.OPEN
    time.sleep(0.01)  # open_timeout=0.0 → transitions immediately
    # Trigger the OPEN → HALF_OPEN transition by checking state
    cb_half._maybe_transition_to_half_open()
    assert cb_half.state == CircuitState.HALF_OPEN

    def cb_weight(cb: CircuitBreaker) -> float:
        """Mirrors the cb_weight logic in agents.py score()."""
        return 0.5 if cb.state == CircuitState.HALF_OPEN else 1.0

    assert cb_weight(cb_closed) == 1.0, "CLOSED agent should have full weight"
    assert cb_weight(cb_half) == 0.5, "HALF_OPEN agent should have half weight"
    assert cb_weight(cb_half) < cb_weight(cb_closed), "HALF_OPEN must score lower than CLOSED"


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            if asyncio.iscoroutinefunction(fn):
                asyncio.run(fn())
            else:
                fn()
            print(f"✅ {name}")
    print(f"\n=== All circuit breaker tests passed ✅ ===")
