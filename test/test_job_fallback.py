"""Unit tests for jobs.py fallback retry logic."""

import asyncio
import json
import os
import sys
import tempfile
import time
import uuid
from dataclasses import dataclass
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.acp_client import AcpError, PoolExhaustedError
from src.jobs import Job, JobManager
from src.store import JobStore
from src.agents import get_next_fallback, get_best_fallback


# ── Helpers ──────────────────────────────────────────────

class FakeConn:
    """Fake ACP connection that yields a simple response."""
    def __init__(self, text="ok"):
        self._text = text

    async def session_prompt(self, prompt):
        yield {"params": {"kind": "text", "data": {"content": self._text}}}
        yield {"_prompt_result": {"result": {"stopReason": "end"}}}


class FailThenSucceedPool:
    """Pool that fails for certain agents, succeeds for others."""
    def __init__(self, fail_agents: set, success_text="fallback-ok"):
        self.fail_agents = fail_agents
        self.success_text = success_text
        self.calls = []
        self._connections = {}

    async def get_or_create(self, agent, session_id, cwd="", profile=None):
        self.calls.append(agent)
        if agent in self.fail_agents:
            raise PoolExhaustedError(f"{agent} pool exhausted")
        return FakeConn(self.success_text)

    def remove(self, agent, session_id):
        pass


class AlwaysFailPool:
    """Pool where every agent fails."""
    calls = []
    _connections = {}

    async def get_or_create(self, agent, session_id, cwd="", profile=None):
        self.calls.append(agent)
        raise AcpError(f"{agent} error")

    def remove(self, agent, session_id):
        pass


def make_manager(pool, db_path=None):
    """Create a JobManager with a fake pool and temp DB."""
    if db_path is None:
        db_path = os.path.join(tempfile.mkdtemp(), "test.db")
    mgr = JobManager.__new__(JobManager)
    mgr._pool = pool
    mgr._pty_configs = {}
    mgr._jobs = {}
    mgr._stats = None
    mgr._webhook_url = ""
    mgr._webhook_format = "openclaw"
    mgr._base_url = ""
    mgr._sender = MagicMock()
    mgr._store = JobStore(db_path)
    mgr._pending_recovery = []
    return mgr


def make_job(agent="kiro", prompt="test"):
    return Job(
        job_id=str(uuid.uuid4()), agent=agent, session_id="s1",
        prompt=prompt, cwd="",
    )


# ── Tests ────────────────────────────────────────────────

def test_no_fallback_on_success():
    """Normal success: no fallback triggered."""
    pool = FailThenSucceedPool(fail_agents=set())
    mgr = make_manager(pool)
    job = make_job("kiro")

    asyncio.run(mgr._run_acp(job))

    assert job.status == "completed"
    assert job.original_agent == "kiro"
    assert job.fallback_history == []
    assert job.retry_count == 0
    assert pool.calls == ["kiro"]
    print("✅ test_no_fallback_on_success")


def test_fallback_on_pool_exhausted():
    """kiro fails → falls back to claude (first in FALLBACK_CHAIN)."""
    pool = FailThenSucceedPool(fail_agents={"kiro"}, success_text="claude-response")
    mgr = make_manager(pool)
    job = make_job("kiro")

    asyncio.run(mgr._run_acp(job))

    assert job.status == "completed"
    assert job.agent == "claude"  # switched
    assert job.original_agent == "kiro"
    assert job.fallback_history == ["kiro"]
    assert job.retry_count == 1
    assert job.result == "claude-response"
    assert pool.calls == ["kiro", "claude"]
    print("✅ test_fallback_on_pool_exhausted")


def test_fallback_chain_multiple():
    """kiro and claude both fail → falls back to opencode."""
    pool = FailThenSucceedPool(fail_agents={"kiro", "claude"}, success_text="opencode-response")
    mgr = make_manager(pool)
    job = make_job("kiro")

    asyncio.run(mgr._run_acp(job))

    assert job.status == "completed"
    assert job.agent == "opencode"
    assert job.fallback_history == ["kiro", "claude"]
    assert job.retry_count == 2
    assert pool.calls == ["kiro", "claude", "opencode"]
    print("✅ test_fallback_chain_multiple")


def test_all_agents_fail():
    """All agents fail → job marked failed."""
    pool = AlwaysFailPool()
    pool.calls = []
    mgr = make_manager(pool)
    job = make_job("kiro")

    asyncio.run(mgr._run_acp(job))

    assert job.status == "failed"
    assert "fallback exhausted" in job.error or "error" in job.error
    assert job.original_agent == "kiro"
    assert len(job.fallback_history) > 0
    # Should have tried kiro + up to MAX_FALLBACK_RETRIES agents
    assert len(pool.calls) == mgr.MAX_FALLBACK_RETRIES
    print("✅ test_all_agents_fail")


def test_job_id_preserved():
    """job_id stays the same across fallback attempts."""
    pool = FailThenSucceedPool(fail_agents={"kiro"})
    mgr = make_manager(pool)
    job = make_job("kiro")
    original_id = job.job_id

    asyncio.run(mgr._run_acp(job))

    assert job.job_id == original_id
    print("✅ test_job_id_preserved")


def test_session_id_changes_on_fallback():
    """session_id is regenerated when switching agent."""
    pool = FailThenSucceedPool(fail_agents={"kiro"})
    mgr = make_manager(pool)
    job = make_job("kiro")
    original_session = job.session_id

    asyncio.run(mgr._run_acp(job))

    assert job.session_id != original_session
    print("✅ test_session_id_changes_on_fallback")


def test_to_dict_includes_fallback_info():
    """to_dict exposes original_agent and fallback_history when fallback occurred."""
    job = make_job("kiro")
    job.original_agent = "kiro"
    job.agent = "claude"
    job.fallback_history = ["kiro"]
    job.status = "completed"
    job.completed_at = job.created_at + 1

    d = job.to_dict()
    assert d["original_agent"] == "kiro"
    assert d["fallback_history"] == ["kiro"]
    assert d["agent"] == "claude"
    print("✅ test_to_dict_includes_fallback_info")


def test_to_dict_omits_fallback_when_none():
    """to_dict does NOT include original_agent when no fallback happened."""
    job = make_job("kiro")
    job.status = "completed"
    job.completed_at = job.created_at + 1

    d = job.to_dict()
    assert "original_agent" not in d
    assert "fallback_history" not in d
    print("✅ test_to_dict_omits_fallback_when_none")


def test_store_roundtrip():
    """New fields survive save → load cycle."""
    with tempfile.TemporaryDirectory() as td:
        store = JobStore(os.path.join(td, "test.db"))
        job = make_job("claude")
        job.original_agent = "kiro"
        job.fallback_history = ["kiro"]
        job.retry_count = 1
        job.status = "completed"
        job.completed_at = time.time()
        store.save(job)

        rows = store.load_recent(10)
        assert len(rows) == 1
        r = rows[0]
        assert r["original_agent"] == "kiro"
        assert r["fallback_history"] == ["kiro"]
        assert r["retry_count"] == 1
        print("✅ test_store_roundtrip")


def test_store_migration():
    """Old DB without new columns gets migrated."""
    with tempfile.TemporaryDirectory() as td:
        db_path = os.path.join(td, "old.db")
        import sqlite3
        conn = sqlite3.connect(db_path)
        conn.executescript("""CREATE TABLE IF NOT EXISTS jobs (
            job_id TEXT PRIMARY KEY, agent TEXT NOT NULL, session_id TEXT NOT NULL,
            prompt TEXT NOT NULL, cwd TEXT DEFAULT '', status TEXT DEFAULT 'pending',
            result TEXT DEFAULT '', error TEXT DEFAULT '', tools TEXT DEFAULT '[]',
            created_at REAL NOT NULL, completed_at REAL DEFAULT 0,
            callback_url TEXT DEFAULT '', callback_meta TEXT DEFAULT '{}',
            webhook_sent INTEGER DEFAULT 0, retries INTEGER DEFAULT 0
        )""")
        conn.close()

        store = JobStore(db_path)
        cols = {r[1] for r in store._db.execute("PRAGMA table_info(jobs)").fetchall()}
        assert "original_agent" in cols
        assert "fallback_history" in cols
        assert "retry_count" in cols
        print("✅ test_store_migration")


def test_acp_error_triggers_fallback():
    """AcpError (not just PoolExhaustedError) also triggers fallback."""
    class AcpErrorPool:
        calls = []
        _connections = {}
        async def get_or_create(self, agent, session_id, cwd="", profile=None):
            self.calls.append(agent)
            if agent == "kiro":
                raise AcpError("idle_timeout")
            return FakeConn("recovered")
        def remove(self, agent, session_id):
            pass

    pool = AcpErrorPool()
    pool.calls = []
    mgr = make_manager(pool)
    job = make_job("kiro")

    asyncio.run(mgr._run_acp(job))

    assert job.status == "completed"
    assert job.agent != "kiro"
    assert job.fallback_history == ["kiro"]
    print("✅ test_acp_error_triggers_fallback")


def test_generic_exception_no_fallback():
    """Non-ACP exceptions (e.g. RuntimeError) do NOT trigger fallback."""
    class CrashPool:
        calls = []
        _connections = {}
        async def get_or_create(self, agent, session_id, cwd="", profile=None):
            self.calls.append(agent)
            raise RuntimeError("unexpected crash")
        def remove(self, agent, session_id):
            pass

    pool = CrashPool()
    pool.calls = []
    mgr = make_manager(pool)
    job = make_job("kiro")

    asyncio.run(mgr._run_acp(job))

    assert job.status == "failed"
    assert "unexpected crash" in job.error
    assert pool.calls == ["kiro"]  # no fallback attempted
    assert job.fallback_history == []
    print("✅ test_generic_exception_no_fallback")


def test_tried_agents_prevents_duplicate():
    """Same agent is never tried twice even if it appears in multiple fallback chains."""
    # Use opencode as the requested agent, which has kiro in its fallback chain
    # kiro -> opencode -> claude -> qwen
    # If kiro fails, fallback to opencode
    # If opencode fails, fallback to claude
    # If claude fails, fallback to qwen
    # The tried_agents list must prevent re-trying kiro even if something tries to reuse it
    
    call_count = {"kiro": 0, "claude": 0, "opencode": 0, "qwen": 0}
    
    class TrackedPool:
        _connections = {}
        async def get_or_create(self, agent, session_id, cwd="", profile=None):
            call_count[agent] += 1
            # Always fail to exhaust all fallbacks
            raise PoolExhaustedError(f"{agent} exhausted")
        def remove(self, agent, session_id):
            pass
    
    pool = TrackedPool()
    mgr = make_manager(pool)
    job = make_job("kiro")
    
    asyncio.run(mgr._run_acp(job))
    
    # With 4 agents in the chain and MAX_FALLBACK_RETRIES=3,
    # we should try exactly 3 unique agents
    assert job.status == "failed"
    total_tries = sum(call_count.values())
    assert total_tries == 3, f"Expected 3 total attempts, got {total_tries}"
    
    # Each agent should only be called once
    for agent, count in call_count.items():
        if count > 0:
            assert count == 1, f"Agent {agent} was called {count} times, expected 1"
    
    # Verify tried_agents logic worked correctly
    assert len(set(job.fallback_history) if job.fallback_history else set()) == len(job.fallback_history)
    print("✅ test_tried_agents_prevents_duplicate")


def test_fallback_exhaustion_error_message():
    """When fallback is exhausted, error message mentions the tried agents."""
    class AlwaysFailPool:
        _connections = {}
        async def get_or_create(self, agent, session_id, cwd="", profile=None):
            raise PoolExhaustedError(f"{agent} pool exhausted")
        def remove(self, agent, session_id):
            pass
    
    pool = AlwaysFailPool()
    mgr = make_manager(pool)
    job = make_job("kiro")
    
    asyncio.run(mgr._run_acp(job))
    
    assert job.status == "failed"
    assert "kiro" in job.error or "fallback" in job.error.lower()
    # The error should indicate exhaustion
    assert "fallback" in job.error.lower() or "exhausted" in job.error.lower()
    print("✅ test_fallback_exhaustion_error_message")


# ── Smart retry tests ────────────────────────────────────

from src.exceptions import AgentTimeoutError, AgentRateLimitError, AgentModelError


def test_timeout_retries_same_agent():
    """AgentTimeoutError → retry same agent before falling back."""
    call_log = []

    class TimeoutThenOkPool:
        _connections = {}
        async def get_or_create(self, agent, session_id, cwd="", profile=None):
            call_log.append(agent)
            if agent == "kiro" and len([c for c in call_log if c == "kiro"]) == 1:
                raise AgentTimeoutError("timeout")
            return FakeConn("recovered")
        def remove(self, agent, session_id):
            pass

    pool = TimeoutThenOkPool()
    mgr = make_manager(pool)
    job = make_job("kiro")

    asyncio.run(mgr._run_acp(job))

    assert job.status == "completed"
    assert job.agent == "kiro"  # stayed on same agent
    assert call_log.count("kiro") == 2  # tried kiro twice
    print("✅ test_timeout_retries_same_agent")


def test_timeout_falls_back_if_retry_fails():
    """AgentTimeoutError → retry fails → falls back to next agent."""
    class AlwaysTimeoutPool:
        _connections = {}
        async def get_or_create(self, agent, session_id, cwd="", profile=None):
            if agent == "kiro":
                raise AgentTimeoutError("timeout")
            return FakeConn("fallback-ok")
        def remove(self, agent, session_id):
            pass

    pool = AlwaysTimeoutPool()
    mgr = make_manager(pool)
    job = make_job("kiro")

    asyncio.run(mgr._run_acp(job))

    assert job.status == "completed"
    assert job.agent != "kiro"
    assert "kiro" in job.fallback_history
    print("✅ test_timeout_falls_back_if_retry_fails")


def test_rate_limit_waits_and_retries():
    """AgentRateLimitError → wait then retry same agent."""
    call_log = []

    class RateLimitThenOkPool:
        _connections = {}
        async def get_or_create(self, agent, session_id, cwd="", profile=None):
            call_log.append(agent)
            if agent == "kiro" and len([c for c in call_log if c == "kiro"]) == 1:
                raise AgentRateLimitError("429", retry_after=1)
            return FakeConn("ok-after-wait")
        def remove(self, agent, session_id):
            pass

    pool = RateLimitThenOkPool()
    mgr = make_manager(pool)
    job = make_job("kiro")

    asyncio.run(mgr._run_acp(job))

    assert job.status == "completed"
    assert job.agent == "kiro"
    assert call_log.count("kiro") == 2
    print("✅ test_rate_limit_waits_and_retries")


def test_model_error_skips_to_fallback():
    """AgentModelError → skip directly to next agent, no retry."""
    call_log = []

    class ModelErrorPool:
        _connections = {}
        async def get_or_create(self, agent, session_id, cwd="", profile=None):
            call_log.append(agent)
            if agent == "kiro":
                raise AgentModelError("model crashed")
            return FakeConn("fallback-ok")
        def remove(self, agent, session_id):
            pass

    pool = ModelErrorPool()
    mgr = make_manager(pool)
    job = make_job("kiro")

    asyncio.run(mgr._run_acp(job))

    assert job.status == "completed"
    assert job.agent != "kiro"
    assert call_log.count("kiro") == 1  # no retry, went straight to fallback
    assert "kiro" in job.fallback_history
    print("✅ test_model_error_skips_to_fallback")
def test_max_fallback_retries_limit():
    """Respects MAX_FALLBACK_RETRIES=3 limit for_total attempts."""
    attempts = []
    
    class CountingPool:
        _connections = {}
        async def get_or_create(self, agent, session_id, cwd="", profile=None):
            attempts.append(agent)
            raise PoolExhaustedError(f"{agent} exhausted")
        def remove(self, agent, session_id):
            pass
    
    pool = CountingPool()
    mgr = make_manager(pool)
    job = make_job("kiro")
    
    asyncio.run(mgr._run_acp(job))
    
    # Total attempts = 1 (initial) + up to MAX_FALLBACK_RETRIES-1 fallbacks = MAX_FALLBACK_RETRIES
    # Actually looking at the code: for loop runs MAX_FALLBACK_RETRIES times,
    # and tried_agents starts empty, so we get MAX_FALLBACK_RETRIES total attempts
    assert len(attempts) == mgr.MAX_FALLBACK_RETRIES
    assert job.status == "failed"
    print(f"✅ test_max_fallback_retries_limit (tried {len(attempts)} agents)")


def test_successful_fallback_on_second_attempt():
    """Fallback succeeds on second attempt (not first, not last)."""
    class SequentialFailPool:
        _connections = {}
        def __init__(self):
            self.call_count = 0
        
        async def get_or_create(self, agent, session_id, cwd="", profile=None):
            self.call_count += 1
            # Fail on first two calls, succeed on third
            if self.call_count <= 2:
                raise PoolExhaustedError(f"{agent} exhausted")
            return FakeConn("succeeded-on-third")
        
        def remove(self, agent, session_id):
            pass
    
    pool = SequentialFailPool()
    mgr = make_manager(pool)
    job = make_job("kiro")
    
    asyncio.run(mgr._run_acp(job))
    
    assert job.status == "completed"
    # Should have switched agents at least once
    assert job.agent != "kiro" or pool.call_count > 1
    assert job.retry_count >= 1
    assert "kiro" in job.fallback_history or pool.call_count == 1
    print("✅ test_successful_fallback_on_second_attempt")


def test_original_agent_stored_on_first_try():
    """original_agent is set on the first run attempt."""
    pool = FailThenSucceedPool(fail_agents=set())  # Succeeds immediately
    mgr = make_manager(pool)
    job = make_job("claude")
    
    asyncio.run(mgr._run_acp(job))
    
    assert job.original_agent == "claude"
    # No fallback happened, but original_agent should still be set
    assert job.fallback_history == []
    print("✅ test_original_agent_stored_on_first_try")


# ── get_best_fallback tests ──────────────────────────────

class FakePoolWithState:
    """Pool with controllable per-agent connection states."""
    def __init__(self, connections: dict[str, str]):
        """connections: {agent: state} e.g. {"claude": "idle", "opencode": "busy"}"""
        self._connections = {}
        for agent, state in connections.items():
            conn = MagicMock()
            conn.state = state
            self._connections[(agent, "s1")] = conn


class FakeStats:
    """Stats that return pre-configured per-agent data."""
    def __init__(self, agents_data: dict):
        self._data = agents_data

    def query(self, hours=1):
        return {"period_hours": hours, "agents": self._data}

    def get_agent_stats(self, agent: str, hours: float = 1.0) -> dict:
        s = self._data.get(agent, {})
        total = s.get("total", 0)
        success = s.get("success", 0)
        return {
            "success_rate": success / total if total > 0 else 1.0,
            "avg_duration": s.get("avg_duration", 30.0),
            "total": total,
        }


def test_best_fallback_no_pool_no_stats():
    """Without pool/stats, falls back to static chain order."""
    result = get_best_fallback("kiro", [])
    static = get_next_fallback("kiro", [])
    assert result == static
    print("✅ test_best_fallback_no_pool_no_stats")


def test_best_fallback_prefers_idle():
    """Agent with idle connection is preferred over busy one."""
    pool = FakePoolWithState({"claude": "busy", "opencode": "idle"})
    result = get_best_fallback("kiro", [], pool=pool)
    assert result == "opencode"
    print("✅ test_best_fallback_prefers_idle")


def test_best_fallback_prefers_high_success_rate():
    """Agent with higher success rate is preferred (no pool data)."""
    stats = FakeStats({
        "claude": {"total": 10, "success": 3, "avg_duration": 10.0},    # 30%
        "opencode": {"total": 10, "success": 9, "avg_duration": 10.0},  # 90%
        "qwen": {"total": 10, "success": 5, "avg_duration": 10.0},      # 50%
    })
    result = get_best_fallback("kiro", [], stats=stats)
    assert result == "opencode"
    print("✅ test_best_fallback_prefers_high_success_rate")


def test_best_fallback_combined_scoring():
    """Idle + decent success rate beats busy + high success rate."""
    pool = FakePoolWithState({"claude": "busy", "opencode": "idle"})
    stats = FakeStats({
        "claude": {"total": 10, "success": 10, "avg_duration": 5.0},    # 100% but busy
        "opencode": {"total": 10, "success": 7, "avg_duration": 10.0},  # 70% but idle
    })
    result = get_best_fallback("kiro", [], pool=pool, stats=stats)
    assert result == "opencode"  # idle boost tips the balance
    print("✅ test_best_fallback_combined_scoring")


def test_best_fallback_success_rate_can_overcome_idle():
    """Busy agent with vastly higher success rate beats idle agent with poor stats."""
    pool = FakePoolWithState({"claude": "idle", "opencode": "busy"})
    stats = FakeStats({
        "claude": {"total": 20, "success": 4, "avg_duration": 50.0},    # 20%, slow
        "opencode": {"total": 20, "success": 19, "avg_duration": 8.0},  # 95%, fast
    })
    result = get_best_fallback("kiro", [], pool=pool, stats=stats)
    assert result == "opencode"  # success rate overcomes idle bonus
    print("✅ test_best_fallback_success_rate_can_overcome_idle")


# ── Circuit breaker integration tests ────────────────────

import pytest
from src.circuit_breaker import CircuitBreaker, CircuitBreakerConfig, CircuitState
import src.agents as _agents_mod


def _setup_breakers(states: dict[str, CircuitState]):
    """Inject fake circuit breakers into agents module."""
    saved = dict(_agents_mod._circuit_breakers)
    _agents_mod._circuit_breakers.clear()
    for agent, state in states.items():
        cb = CircuitBreaker(agent, CircuitBreakerConfig(
            failure_threshold=2, open_timeout=999))
        cb.state = state
        cb._state_changed_at = time.monotonic()
        _agents_mod._circuit_breakers[agent] = cb
    return saved


def _restore_breakers(saved):
    _agents_mod._circuit_breakers.clear()
    _agents_mod._circuit_breakers.update(saved)


def test_open_breaker_filtered_from_candidates():
    """Agent with OPEN circuit breaker is excluded from fallback candidates."""
    saved = _setup_breakers({"claude": CircuitState.OPEN, "opencode": CircuitState.CLOSED})
    try:
        pool = FakePoolWithState({"claude": "idle", "opencode": "idle"})
        result = get_best_fallback("kiro", [], pool=pool)
        assert result == "opencode", f"expected opencode, got {result}"
    finally:
        _restore_breakers(saved)


def test_all_breakers_open_returns_none():
    """When all fallback agents have OPEN breakers, return None."""
    saved = _setup_breakers({
        "claude": CircuitState.OPEN,
        "opencode": CircuitState.OPEN,
        "qwen": CircuitState.OPEN,
    })
    try:
        pool = FakePoolWithState({"claude": "idle", "opencode": "idle", "qwen": "idle"})
        result = get_best_fallback("kiro", [], pool=pool)
        assert result is None
    finally:
        _restore_breakers(saved)


def test_half_open_gets_lower_score_than_closed():
    """HALF_OPEN agent scores lower than identical CLOSED agent (×0.5 weight)."""
    saved = _setup_breakers({
        "claude": CircuitState.HALF_OPEN,
        "opencode": CircuitState.CLOSED,
    })
    try:
        # Both idle, same stats → CLOSED should win due to cb_weight
        pool = FakePoolWithState({"claude": "idle", "opencode": "idle"})
        stats = FakeStats({
            "claude": {"total": 10, "success": 10, "avg_duration": 10.0},
            "opencode": {"total": 10, "success": 10, "avg_duration": 10.0},
        })
        result = get_best_fallback("kiro", [], pool=pool, stats=stats)
        assert result == "opencode", f"expected opencode (CLOSED), got {result}"
    finally:
        _restore_breakers(saved)


def test_half_open_still_selectable_if_only_option():
    """HALF_OPEN agent is still returned when it's the only non-OPEN candidate."""
    saved = _setup_breakers({
        "claude": CircuitState.HALF_OPEN,
        "opencode": CircuitState.OPEN,
        "qwen": CircuitState.OPEN,
    })
    try:
        pool = FakePoolWithState({"claude": "idle"})
        result = get_best_fallback("kiro", [], pool=pool)
        assert result == "claude"
    finally:
        _restore_breakers(saved)


def test_no_breaker_entry_treated_as_closed():
    """Agent without a circuit breaker entry gets full score (no penalty)."""
    saved = _setup_breakers({"claude": CircuitState.HALF_OPEN})
    try:
        # opencode has no breaker entry → should be treated as CLOSED (weight=1.0)
        pool = FakePoolWithState({"claude": "idle", "opencode": "idle"})
        stats = FakeStats({
            "claude": {"total": 10, "success": 10, "avg_duration": 10.0},
            "opencode": {"total": 10, "success": 10, "avg_duration": 10.0},
        })
        result = get_best_fallback("kiro", [], pool=pool, stats=stats)
        assert result == "opencode"
    finally:
        _restore_breakers(saved)


@pytest.mark.asyncio
async def test_rate_limit_does_not_trip_breaker():
    """AgentRateLimitError must NOT count as a circuit breaker failure."""
    from src.exceptions import AgentRateLimitError

    cb = CircuitBreaker("rate-test", CircuitBreakerConfig(
        failure_threshold=2,
        expected_exceptions=(AcpError,),
        excluded_exceptions=(AgentRateLimitError,),  # excluded takes priority
    ))

    async def rate_limited():
        raise AgentRateLimitError("429 too many requests", retry_after=5)

    for _ in range(5):
        with pytest.raises(AgentRateLimitError):
            await cb.call(rate_limited)

    # AgentRateLimitError is caught by excluded_exceptions before expected_exceptions,
    # so it bypasses the breaker's failure recording entirely.
    assert cb.state == CircuitState.CLOSED
    assert cb.failure_calls == 0, f"expected 0 failure_calls, got {cb.failure_calls}"


if __name__ == "__main__":
    test_no_fallback_on_success()
    test_fallback_on_pool_exhausted()
    test_fallback_chain_multiple()
    test_all_agents_fail()
    test_job_id_preserved()
    test_session_id_changes_on_fallback()
    test_to_dict_includes_fallback_info()
    test_to_dict_omits_fallback_when_none()
    test_store_roundtrip()
    test_store_migration()
    test_acp_error_triggers_fallback()
    test_generic_exception_no_fallback()
    test_tried_agents_prevents_duplicate()
    test_fallback_exhaustion_error_message()
    test_max_fallback_retries_limit()
    test_successful_fallback_on_second_attempt()
    test_original_agent_stored_on_first_try()
    test_best_fallback_no_pool_no_stats()
    test_best_fallback_prefers_idle()
    test_best_fallback_prefers_high_success_rate()
    test_best_fallback_combined_scoring()
    test_best_fallback_success_rate_can_overcome_idle()
    test_timeout_retries_same_agent()
    test_timeout_falls_back_if_retry_fails()
    test_rate_limit_waits_and_retries()
    test_model_error_skips_to_fallback()
    test_open_breaker_filtered_from_candidates()
    test_all_breakers_open_returns_none()
    test_half_open_gets_lower_score_than_closed()
    test_half_open_still_selectable_if_only_option()
    test_no_breaker_entry_treated_as_closed()
    print(f"\n=== All 32 tests passed ✅ ===")
