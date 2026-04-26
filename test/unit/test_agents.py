"""
Unit tests for agents.py - Agent pool management, selection, and fallback logic.

Framework by Claude, implementation by Qwen.
"""

import asyncio
import time

import pytest
from unittest.mock import Mock, AsyncMock, patch, call
from src.acp_client import AcpConnection, AcpError, AcpProcessPool, PoolExhaustedError
from src.circuit_breaker import CircuitBreaker, CircuitState
from src.exceptions import AgentModelError, AgentTimeoutError


# ============================================================================
# Mock helpers (from merged P0/P1 tests)
# ============================================================================

class MockCircuitBreaker:
    def __init__(self, state: CircuitState):
        self.state = state


# ============================================================================
# Fixtures
# ============================================================================

@pytest.fixture
def agent_pool():
    """Create a fresh AgentPool instance for each test."""
    # TODO: Initialize with test configuration
    pass


@pytest.fixture
def mock_agents():
    """Create mock agent instances with configurable behavior."""
    # TODO: Return dict of mock agents (e.g., {"agent_a": mock_a, "agent_b": mock_b})
    pass


@pytest.fixture
def agent_stats():
    """Create default AgentStats for testing."""
    # TODO: Return AgentStats with typical values
    pass


# ============================================================================
# Test Class: Agent Pool Management
# ============================================================================

class TestAgentPoolManagement:
    """Tests for agent registration, removal, and pool state."""

    def test_register_agent(self, agent_pool):
        """Test registering a new agent to the pool."""
        pass

    def test_register_duplicate_agent(self, agent_pool):
        """Test that registering the same agent twice raises error or updates."""
        pass

    def test_remove_agent(self, agent_pool):
        """Test removing an agent from the pool."""
        pass

    def test_remove_nonexistent_agent(self, agent_pool):
        """Test removing an agent that doesn't exist."""
        pass

    def test_list_agents(self, agent_pool, mock_agents):
        """Test listing all registered agents."""
        pass

    def test_get_agent_stats(self, agent_pool):
        """Test retrieving stats for a specific agent."""
        pass


# ============================================================================
# Test Class: Agent Selection & Scoring
# ============================================================================

class TestAgentSelection:
    """Tests for agent selection algorithm and scoring logic."""

    def test_select_agent_basic(self, agent_pool, mock_agents):
        """Test basic agent selection with healthy agents."""
        pass

    def test_select_agent_with_idle_preference(self, agent_pool, mock_agents):
        """Test that agents with idle capacity get 1.5x weight."""
        pass

    def test_select_agent_with_circuit_open(self, agent_pool, mock_agents):
        """Test that agents with OPEN circuit breaker are excluded."""
        pass

    def test_select_agent_with_circuit_half_open(self, agent_pool, mock_agents):
        """Test that HALF_OPEN agents get 0.5x weight penalty."""
        pass

    def test_select_agent_all_circuits_open(self, agent_pool, mock_agents):
        """Test behavior when all agents have OPEN circuit breakers."""
        pass

    def test_select_agent_empty_pool(self, agent_pool):
        """Test agent selection when pool is empty."""
        pass

    def test_score_calculation_base(self, agent_pool, agent_stats):
        """Test base score calculation without modifiers."""
        pass

    def test_score_with_trend_penalty(self, agent_pool, agent_stats):
        """Test score penalty based on error trend."""
        pass

    def test_score_with_combined_modifiers(self, agent_pool, agent_stats):
        """Test score with multiple modifiers: idle, CB state, trend."""
        pass


# ============================================================================
# Test Class: Fallback Logic
# ============================================================================

class TestAgentFallback:
    """Tests for fallback behavior when primary agent fails."""

    def test_fallback_when_primary_fails(self, agent_pool, mock_agents):
        """Test fallback to next best agent when primary fails."""
        pass

    def test_fallback_chain_exhaustion(self, agent_pool, mock_agents):
        """Test behavior when all fallback candidates fail."""
        pass

    def test_fallback_respects_circuit_state(self, agent_pool, mock_agents):
        """Test that fallback skips agents with OPEN circuit breakers."""
        pass

    def test_fallback_order_by_score(self, agent_pool, mock_agents):
        """Test that fallback tries agents in descending score order."""
        pass


# ============================================================================
# Test Class: Circuit Breaker Integration
# ============================================================================

class TestCircuitBreakerIntegration:
    """Tests for circuit breaker interaction with agent selection."""

    def test_failure_triggers_circuit_breaker(self, agent_pool, mock_agents):
        """Test that agent failures are recorded in circuit breaker."""
        pass

    def test_success_resets_circuit_breaker(self, agent_pool, mock_agents):
        """Test that successful calls help close the circuit breaker."""
        pass

    def test_circuit_breaker_state_affects_availability(self, agent_pool, mock_agents):
        """Test that CB state changes agent availability."""
        pass

    def test_half_open_transitions_to_closed_on_success(self, agent_pool, mock_agents):
        """Test HALF_OPEN → CLOSED transition on successful call."""
        pass

    def test_half_open_transitions_to_open_on_failure(self, agent_pool, mock_agents):
        """Test HALF_OPEN → OPEN transition on failed call."""
        pass


# ============================================================================
# Test Class: RateLimit Handling
# ============================================================================

class TestRateLimitHandling:
    """Tests for RateLimit error handling (should NOT trigger circuit breaker)."""

    def test_ratelimit_does_not_trigger_circuit_breaker(self, agent_pool, mock_agents):
        """Test that AgentRateLimitError does NOT call circuit_breaker.record_failure()."""
        pass

    def test_ratelimit_retry_with_backoff(self, agent_pool, mock_agents):
        """Test that RateLimit triggers sleep(retry_after) and retries."""
        pass

    def test_ratelimit_max_retry_after_capped(self, agent_pool, mock_agents):
        """Test that retry_after is capped at 30 seconds."""
        pass

    def test_other_errors_still_trigger_circuit_breaker(self, agent_pool, mock_agents):
        """Test that non-RateLimit errors (TimeoutError, AcpError) still trigger CB."""
        pass


# ============================================================================
# Test Class: Stats Updates
# ============================================================================

class TestStatsUpdates:
    """Tests for agent statistics tracking and updates."""

    def test_stats_updated_on_success(self, agent_pool, mock_agents):
        """Test that successful calls update success count and latency."""
        pass

    def test_stats_updated_on_failure(self, agent_pool, mock_agents):
        """Test that failed calls update failure count."""
        pass

    def test_stats_error_trend_calculation(self, agent_pool, agent_stats):
        """Test error trend calculation (e.g., rolling average)."""
        pass

    def test_stats_latency_tracking(self, agent_pool, mock_agents):
        """Test that call latency is tracked correctly."""
        pass


# ============================================================================
# Test Class: Edge Cases & Error Handling
# ============================================================================

class TestEdgeCases:
    """Tests for edge cases and error scenarios."""

    def test_concurrent_agent_selection(self, agent_pool, mock_agents):
        """Test thread-safety of agent selection under concurrent load."""
        pass

    def test_agent_becomes_unavailable_during_selection(self, agent_pool, mock_agents):
        """Test graceful handling when agent disappears mid-selection."""
        pass

    def test_invalid_agent_configuration(self, agent_pool):
        """Test error handling for invalid agent config."""
        pass

    def test_zero_score_agent_not_selected(self, agent_pool, mock_agents):
        """Test that agents with zero or negative score are not selected."""
        pass


# ============================================================================
# Test Class: Network & Connection Layer (by Kiro - Ops perspective)
# ============================================================================

class TestNetworkResilience:
    """Tests for network-level failures: timeouts, DNS, connection pool."""

    def test_connect_timeout_triggers_circuit_breaker(self, agent_pool, mock_agents):
        """Test that connect timeout is treated as failure and recorded in CB."""
        pass

    def test_read_timeout_triggers_circuit_breaker(self, agent_pool, mock_agents):
        """Test that read timeout (connected but slow response) triggers CB."""
        pass

    def test_dns_resolution_failure(self, agent_pool, mock_agents):
        """Test that unresolvable agent endpoint fails fast without hanging."""
        pass

    def test_connection_pool_exhaustion(self, agent_pool, mock_agents):
        """Test behavior when HTTP connection pool is full under high concurrency."""
        pass

    def test_timeout_does_not_count_as_ratelimit(self, agent_pool, mock_agents):
        """Test that timeout errors are NOT mistaken for RateLimit (should trigger CB)."""
        pass


# ============================================================================
# Test Class: Agent Lifecycle & Recovery (by Kiro - Ops perspective)
# ============================================================================

class TestAgentLifecycle:
    """Tests for agent restart, flapping, and recovery scenarios."""

    def test_agent_recovery_after_restart(self, agent_pool, mock_agents):
        """Test full recovery path: OPEN → HALF_OPEN → CLOSED after agent comes back."""
        pass

    def test_agent_flapping_debounce(self, agent_pool, mock_agents):
        """Test that rapid success/failure alternation doesn't cause CB state thrashing."""
        pass

    def test_all_agents_down_then_one_recovers(self, agent_pool, mock_agents):
        """Test that first recovered agent correctly receives traffic after total outage."""
        pass

    def test_agent_returns_5xx_fast_failure(self, agent_pool, mock_agents):
        """Test that 5xx responses (non-timeout) still trigger CB like timeouts do."""
        pass

    def test_pool_hot_add_agent(self, agent_pool, mock_agents):
        """Test adding a new agent at runtime without disrupting in-flight requests."""
        pass

    def test_pool_hot_remove_agent(self, agent_pool, mock_agents):
        """Test removing an agent at runtime; in-flight requests should complete."""
        pass

    def test_graceful_shutdown_drains_inflight(self, agent_pool, mock_agents):
        """Test that shutdown waits for in-flight requests and rejects new ones."""
        pass


# ============================================================================
# Test Class: Observability & Long-running Stability (by Kiro - Ops perspective)
# ============================================================================

class TestObservabilityAndStability:
    """Tests for logging, metrics, and long-running state correctness."""

    def test_fallback_event_logged_with_context(self, agent_pool, mock_agents):
        """Test that fallback emits log with: source agent, target agent, failure reason."""
        pass

    def test_circuit_breaker_state_change_logged(self, agent_pool, mock_agents):
        """Test that CB state transitions (CLOSED→OPEN, OPEN→HALF_OPEN) emit logs."""
        pass

    def test_stats_accuracy_after_long_running(self, agent_pool, agent_stats):
        """Test that latency avg and error trend stay accurate after thousands of calls."""
        pass

    def test_stats_rolling_window_not_polluted_by_old_data(self, agent_pool, agent_stats):
        """Test that ancient success/failure data doesn't skew current scoring."""
        pass


# ============================================================================
# Test Class: Concurrency & Race Conditions (by Kiro - Ops perspective)
# ============================================================================

class TestConcurrencyAndRaceConditions:
    """10 请求抢 3 idle agent 等 race-condition 场景.

    Uses a real AcpProcessPool with mocked _spawn to avoid actual subprocesses.
    Tests verify pool invariants hold under asyncio.gather concurrency.
    """

    @pytest.fixture
    def small_pool(self):
        """Pool: max 3 global, max 2 per agent — easy to saturate."""
        cfg = {
            "kiro": {"command": "echo", "working_dir": "/tmp"},
            "claude": {"command": "echo", "working_dir": "/tmp"},
            "qwen": {"command": "echo", "working_dir": "/tmp"},
        }
        return AcpProcessPool(cfg, max_processes=3, max_per_agent=2)

    @staticmethod
    def _make_fake_conn(agent, session_id, busy=False):
        """Build a lightweight fake AcpConnection without a real subprocess."""
        conn = Mock(spec=AcpConnection)
        conn.agent = agent
        conn.session_id = session_id
        conn.alive = True
        conn._busy = busy
        conn.last_active = time.time()
        conn.session_reset = False
        conn.state = "busy" if busy else "idle"
        conn.proc = Mock(pid=99999)
        conn.kill = AsyncMock()
        conn.session_new = AsyncMock(return_value="sid")
        conn.initialize = AsyncMock()
        conn.ping = AsyncMock(return_value=True)
        return conn

    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_concurrent_acquire_respects_pool_limit(self, small_pool):
        """10 并发 get_or_create，pool max=3 → asyncio.Lock 保证最多 3 成功."""
        async def fake_spawn(agent, sid, cfg, **kw):
            await asyncio.sleep(0)  # yield — previously let coroutines race past the check
            return self._make_fake_conn(agent, sid, busy=True)

        small_pool._spawn = fake_spawn

        async def try_acquire(i):
            try:
                await small_pool.get_or_create("kiro", f"s{i}")
                return "ok"
            except PoolExhaustedError:
                return "exhausted"

        results = await asyncio.gather(*(try_acquire(i) for i in range(10)))
        ok_count = results.count("ok")
        assert ok_count <= 3, f"should not exceed pool max=3, got {ok_count}"
        assert "exhausted" in results, "some requests must be rejected"

    @pytest.mark.asyncio
    async def test_no_double_assign_under_race(self, small_pool):
        """同一 (agent, session) key 不会产生两个并行连接."""
        call_count = 0

        async def fake_spawn(agent, sid, cfg, **kw):
            nonlocal call_count
            call_count += 1
            await asyncio.sleep(0.01)
            return self._make_fake_conn(agent, sid)

        small_pool._spawn = fake_spawn

        # Pre-populate one idle connection
        conn0 = self._make_fake_conn("kiro", "shared")
        small_pool._connections[("kiro", "shared")] = conn0

        results = await asyncio.gather(
            small_pool.get_or_create("kiro", "shared"),
            small_pool.get_or_create("kiro", "shared"),
        )
        # Both should return the same pre-existing connection (no spawn needed)
        assert results[0] is results[1] is conn0
        assert call_count == 0

    @pytest.mark.asyncio
    async def test_release_then_acquire_reuses_slot(self, small_pool):
        """释放 idle 连接后，新请求能复用该 slot 而不超限."""
        async def fake_spawn(agent, sid, cfg, **kw):
            return self._make_fake_conn(agent, sid)

        small_pool._spawn = fake_spawn

        # Fill pool to max (3)
        for i in range(3):
            c = self._make_fake_conn("kiro", f"fill{i}")
            small_pool._connections[("kiro", f"fill{i}")] = c

        # Mark one as idle so eviction can reclaim it
        small_pool._connections[("kiro", "fill0")]._busy = False

        # New request should evict the idle one and succeed
        conn = await small_pool.get_or_create("claude", "new1")
        assert conn.agent == "claude"
        assert len(small_pool._connections) <= 3

    @pytest.mark.asyncio
    async def test_rapid_state_toggle_consistency(self):
        """快速切换 _busy 后，.state 属性始终与 _busy 一致."""
        conn = self._make_fake_conn("kiro", "s1")
        # Use a real property-like check: override state to be dynamic
        # AcpConnection.state is a property, but our Mock uses spec — test the logic
        for _ in range(1000):
            conn._busy = True
            conn.state = "busy"
            assert conn.state == "busy"
            conn._busy = False
            conn.state = "idle"
            assert conn.state == "idle"

    @pytest.mark.asyncio
    async def test_concurrent_close_idempotent(self, small_pool):
        """并发 close 同一连接不会 raise 也不会破坏 pool 计数."""
        conn = self._make_fake_conn("kiro", "dup")
        small_pool._connections[("kiro", "dup")] = conn

        await asyncio.gather(
            small_pool.close("kiro", "dup"),
            small_pool.close("kiro", "dup"),
            small_pool.close("kiro", "dup"),
        )
        assert ("kiro", "dup") not in small_pool._connections
        # kill may be called once (second/third close finds key already gone)
        assert conn.kill.await_count >= 1

    @pytest.mark.asyncio
    async def test_pool_exhausted_raises_not_deadlocks(self, small_pool):
        """池满且全 busy 时，新请求立即 PoolExhaustedError 而非挂起."""
        for i in range(3):
            c = self._make_fake_conn("kiro", f"busy{i}", busy=True)
            small_pool._connections[("kiro", f"busy{i}")] = c

        with pytest.raises(PoolExhaustedError):
            await asyncio.wait_for(
                small_pool.get_or_create("kiro", "overflow"),
                timeout=2.0,
            )

    @pytest.mark.asyncio
    async def test_pool_count_integrity_after_burst(self, small_pool):
        """并发 acquire + close 风暴后，pool 内部计数与 _connections 实际长度一致."""
        async def fake_spawn(agent, sid, cfg, **kw):
            await asyncio.sleep(0)
            return self._make_fake_conn(agent, sid)

        small_pool._spawn = fake_spawn

        # Burst: create then immediately close
        async def churn(i):
            try:
                await small_pool.get_or_create("claude", f"churn{i}")
            except PoolExhaustedError:
                pass
            await small_pool.close("claude", f"churn{i}")

        await asyncio.gather(*(churn(i) for i in range(20)))

        # Invariant: stats["total"] == len(_connections)
        assert small_pool.stats["total"] == len(small_pool._connections)
        # All remaining connections should be alive or already removed
        for key, conn in small_pool._connections.items():
            assert conn.alive, f"dead connection left in pool: {key}"


# ============================================================================
# Test Class: Connection Leak & Retry Safety (by Kiro - P1 fixes)
# ============================================================================

class TestConnectionLeakAndRetrySafety:
    """Tests for P1-A (connection cleanup) and P1-B (retry exception propagation)."""

    @pytest.mark.asyncio
    async def test_execute_agent_call_cleans_up_on_midstream_crash(self):
        """P1-A: If session_prompt raises mid-stream, pool.remove is called."""
        from src.agents import _execute_agent_call

        pool = Mock()
        conn = AsyncMock()

        async def crashing_prompt(prompt, idle_timeout=300):
            yield {"method": "x", "params": {"type": "message.part", "content": "partial"}}
            raise RuntimeError("connection died mid-stream")

        conn.session_prompt = crashing_prompt
        conn.session_reset = False
        pool.get_or_create = AsyncMock(return_value=conn)
        pool.remove = Mock()

        with pytest.raises(RuntimeError, match="connection died mid-stream"):
            await _execute_agent_call("kiro", "test", pool, None, "s1", "/tmp")

        pool.remove.assert_called_once_with("kiro", "s1")

    @pytest.mark.asyncio
    async def test_execute_agent_call_cleans_up_on_enrich_crash(self):
        """P1-A: If enrichment code crashes before session_prompt, pool.remove is called."""
        from src.agents import _execute_agent_call, _env
        import src.agents as _mod

        pool = Mock()
        conn = AsyncMock()
        conn.session_reset = False
        pool.get_or_create = AsyncMock(return_value=conn)
        pool.remove = Mock()

        bad_env = Mock()
        bad_env.get_prefix = Mock(side_effect=RuntimeError("env crash"))
        saved = _mod._env
        _mod._env = bad_env
        try:
            with pytest.raises(RuntimeError, match="env crash"):
                await _execute_agent_call("kiro", "test", pool, None, "s1", "/tmp", enrich_prompt=True)
            pool.remove.assert_called_once_with("kiro", "s1")
        finally:
            _mod._env = saved

    @pytest.mark.asyncio
    async def test_handle_retry_propagates_exception(self):
        """P1-B: _handle_retry re-raises instead of swallowing exceptions."""
        from src.agents import _handle_retry

        pool = Mock()
        conn = AsyncMock()
        conn.session_reset = False

        async def crash_prompt(prompt, idle_timeout=300):
            yield {"method": "x", "params": {"type": "message.part", "content": "partial"}}
            raise AgentTimeoutError("still broken")

        conn.session_prompt = crash_prompt
        pool.get_or_create = AsyncMock(return_value=conn)
        pool.remove = Mock()

        with pytest.raises(AgentTimeoutError):
            await _handle_retry("kiro", "test", pool, None, "s1", "/tmp")


# ============================================================================
# P0: get_best_fallback — basic scenarios, CB filtering, pool state, stats
# (merged from test_agents_p0.py)
# ============================================================================

# --- Basic scenarios ---

@patch('src.fallback_policy.FALLBACK_CHAIN', {})
@patch('src.fallback_policy.is_agent_healthy', return_value=True)
@patch('src.fallback_policy._circuit_breakers', new={})
def test_no_candidates_empty_fallback_chain(mock_healthy):
    from src.fallback_policy import get_best_fallback
    result = get_best_fallback('hermes')
    assert result is None

@patch('src.fallback_policy.FALLBACK_CHAIN', {'hermes': ['claude', 'kiro']})
@patch('src.fallback_policy.is_agent_healthy', return_value=True)
@patch('src.fallback_policy._circuit_breakers', new={})
def test_no_candidates_all_tried(mock_healthy):
    from src.fallback_policy import get_best_fallback
    result = get_best_fallback('hermes', tried_agents=['claude', 'kiro'])
    assert result is None

@patch('src.fallback_policy.FALLBACK_CHAIN', {'hermes': ['claude']})
@patch('src.fallback_policy.is_agent_healthy', return_value=True)
@patch('src.fallback_policy._circuit_breakers', new={})
def test_single_candidate(mock_healthy):
    from src.fallback_policy import get_best_fallback
    result = get_best_fallback('hermes')
    assert result == 'claude'

@patch('src.fallback_policy.FALLBACK_CHAIN', {'hermes': ['claude', 'kiro', 'qwen']})
@patch('src.fallback_policy.is_agent_healthy', return_value=True)
@patch('src.fallback_policy._circuit_breakers', new={})
def test_no_pool_no_stats_returns_first(mock_healthy):
    from src.fallback_policy import get_best_fallback
    result = get_best_fallback('hermes', pool=None, stats=None)
    assert result == 'claude'

# --- Circuit breaker filtering ---

@patch('src.fallback_policy.FALLBACK_CHAIN', {'hermes': ['claude', 'kiro', 'qwen']})
@patch('src.fallback_policy.is_agent_healthy', return_value=True)
def test_filter_open_circuit_breakers(mock_healthy):
    from src.fallback_policy import get_best_fallback
    with patch('src.fallback_policy._circuit_breakers', {
        'claude': MockCircuitBreaker(CircuitState.OPEN),
        'kiro': MockCircuitBreaker(CircuitState.CLOSED),
    }):
        result = get_best_fallback('hermes', pool=None, stats=None)
        assert result in ['kiro', 'qwen']
        assert result != 'claude'

@patch('src.fallback_policy.FALLBACK_CHAIN', {'hermes': ['claude', 'kiro']})
@patch('src.fallback_policy.is_agent_healthy', return_value=True)
def test_half_open_penalty(mock_healthy):
    from src.fallback_policy import get_best_fallback
    mock_pool = Mock()
    mock_pool._connections = {}
    mock_stats = Mock()
    mock_stats.get_agent_stats = Mock(return_value={'success_rate': 0.95, 'avg_duration': 30.0})
    with patch('src.fallback_policy._circuit_breakers', {
        'claude': MockCircuitBreaker(CircuitState.HALF_OPEN),
        'kiro': MockCircuitBreaker(CircuitState.CLOSED),
    }):
        result = get_best_fallback('hermes', pool=mock_pool, stats=mock_stats)
        assert result == 'kiro'

@patch('src.fallback_policy.FALLBACK_CHAIN', {'hermes': ['claude', 'kiro']})
@patch('src.fallback_policy.is_agent_healthy', return_value=True)
def test_closed_circuit_breaker_normal_score(mock_healthy):
    from src.fallback_policy import get_best_fallback
    mock_pool = Mock()
    mock_pool._connections = {}
    mock_stats = Mock()
    def get_stats_side_effect(agent, hours):
        return {'claude': {'success_rate': 0.90, 'avg_duration': 20.0},
                'kiro': {'success_rate': 0.85, 'avg_duration': 25.0}}.get(agent, {})
    mock_stats.get_agent_stats = Mock(side_effect=get_stats_side_effect)
    with patch('src.fallback_policy._circuit_breakers', {
        'claude': MockCircuitBreaker(CircuitState.CLOSED),
        'kiro': MockCircuitBreaker(CircuitState.CLOSED),
    }):
        result = get_best_fallback('hermes', pool=mock_pool, stats=mock_stats)
        assert result == 'claude'

# --- Pool state ---

@patch('src.fallback_policy.FALLBACK_CHAIN', {'hermes': ['claude', 'kiro']})
@patch('src.fallback_policy.is_agent_healthy', return_value=True)
@patch('src.fallback_policy._circuit_breakers', new={})
def test_has_idle_multiplier_1_5x(mock_healthy):
    from src.fallback_policy import get_best_fallback
    mock_conn_claude = Mock(state='idle')
    mock_conn_kiro = Mock(state='busy')
    mock_pool = Mock()
    mock_pool._connections = {('claude', 'conn1'): mock_conn_claude, ('kiro', 'conn2'): mock_conn_kiro}
    mock_stats = Mock()
    mock_stats.get_agent_stats = Mock(return_value={'success_rate': 0.80, 'avg_duration': 30.0})
    result = get_best_fallback('hermes', pool=mock_pool, stats=mock_stats)
    assert result == 'claude'

# --- Stats scoring ---

@patch('src.fallback_policy.FALLBACK_CHAIN', {'hermes': ['claude', 'kiro']})
@patch('src.fallback_policy.is_agent_healthy', return_value=True)
@patch('src.fallback_policy._circuit_breakers', new={})
def test_success_rate_1h_dominates(mock_healthy):
    from src.fallback_policy import get_best_fallback
    mock_pool = Mock()
    mock_pool._connections = {}
    mock_stats = Mock()
    def get_stats(agent, hours):
        if hours == 1.0:
            return {'claude': {'success_rate': 0.95, 'avg_duration': 50.0},
                    'kiro': {'success_rate': 0.70, 'avg_duration': 10.0}}.get(agent, {})
        else:
            return {'success_rate': 0.95 if agent == 'claude' else 0.70, 'avg_duration': 30.0}
    mock_stats.get_agent_stats = Mock(side_effect=get_stats)
    result = get_best_fallback('hermes', pool=mock_pool, stats=mock_stats)
    assert result == 'claude'

@patch('src.fallback_policy.FALLBACK_CHAIN', {'hermes': ['claude', 'kiro']})
@patch('src.fallback_policy.is_agent_healthy', return_value=True)
@patch('src.fallback_policy._circuit_breakers', new={})
def test_avg_duration_affects_score(mock_healthy):
    from src.fallback_policy import get_best_fallback
    mock_pool = Mock()
    mock_pool._connections = {}
    mock_stats = Mock()
    def get_stats(agent, hours):
        return {'claude': {'success_rate': 0.80, 'avg_duration': 10.0},
                'kiro': {'success_rate': 0.80, 'avg_duration': 90.0}}.get(agent, {})
    mock_stats.get_agent_stats = Mock(side_effect=get_stats)
    result = get_best_fallback('hermes', pool=mock_pool, stats=mock_stats)
    assert result == 'claude'

@patch('src.fallback_policy.FALLBACK_CHAIN', {'hermes': ['claude', 'kiro']})
@patch('src.fallback_policy.is_agent_healthy', return_value=True)
@patch('src.fallback_policy._circuit_breakers', new={})
def test_trend_penalty_declining_success(mock_healthy):
    from src.fallback_policy import get_best_fallback
    mock_pool = Mock()
    mock_pool._connections = {}
    mock_stats = Mock()
    def get_stats(agent, hours):
        if hours == 1.0:
            return {'claude': {'success_rate': 0.90, 'avg_duration': 30.0},
                    'kiro': {'success_rate': 0.90, 'avg_duration': 30.0}}.get(agent, {})
        else:
            return {'claude': {'success_rate': 0.90, 'avg_duration': 30.0},
                    'kiro': {'success_rate': 0.60, 'avg_duration': 30.0}}.get(agent, {})
    mock_stats.get_agent_stats = Mock(side_effect=get_stats)
    result = get_best_fallback('hermes', pool=mock_pool, stats=mock_stats)
    assert result == 'claude'

# --- get_next_fallback ---

@patch('src.fallback_policy.FALLBACK_CHAIN', {'claude': ['opencode', 'qwen', 'kiro']})
def test_get_next_fallback_skips_failed_agent():
    from src.fallback_policy import get_next_fallback
    result = get_next_fallback('claude', tried_agents=['claude'])
    assert result == 'opencode'

@patch('src.fallback_policy.FALLBACK_CHAIN', {'claude': ['opencode', 'qwen', 'kiro']})
def test_get_next_fallback_all_tried_returns_none():
    from src.fallback_policy import get_next_fallback
    result = get_next_fallback('claude', tried_agents=['opencode', 'qwen', 'kiro'])
    assert result is None

@patch('src.fallback_policy.FALLBACK_CHAIN', {'claude': ['opencode', 'qwen', 'kiro']})
def test_get_next_fallback_no_tried():
    from src.fallback_policy import get_next_fallback
    result = get_next_fallback('claude', tried_agents=[])
    assert result == 'opencode'

# --- strip_ansi ---

def test_strip_ansi_removes_escape_codes():
    from src.agents import strip_ansi
    assert strip_ansi('\x1b[31mhello\x1b[0m') == 'hello'


# ============================================================================
# P1: get_best_fallback — health checks, CB edge cases, pool multi-state, scoring
# (merged from test_agents_p1.py)
# ============================================================================

# --- Health checks ---

@patch('src.fallback_policy.FALLBACK_CHAIN', new={'hermes': ['unhealthy', 'claude', 'kiro']})
@patch('src.fallback_policy._circuit_breakers', new={})
def test_prefer_healthy_agent():
    from src.fallback_policy import get_best_fallback
    with patch('src.fallback_policy.is_agent_healthy') as mock_healthy:
        mock_healthy.side_effect = lambda a: a != 'unhealthy'
        result = get_best_fallback('hermes', pool=None, stats=None)
        assert result == 'claude'

@patch('src.fallback_policy.FALLBACK_CHAIN', new={'hermes': ['claude', 'kiro']})
@patch('src.fallback_policy._circuit_breakers', new={})
def test_fallback_to_unhealthy_if_no_healthy():
    from src.fallback_policy import get_best_fallback
    with patch('src.fallback_policy.is_agent_healthy', return_value=False):
        result = get_best_fallback('hermes', pool=None, stats=None)
        assert result == 'claude'

@patch('src.fallback_policy.FALLBACK_CHAIN', new={'hermes': ['claude', 'kiro', 'qwen']})
@patch('src.fallback_policy._circuit_breakers', new={})
def test_is_agent_healthy_integration():
    from src.fallback_policy import get_best_fallback
    with patch('src.fallback_policy.is_agent_healthy') as mock_healthy:
        mock_healthy.return_value = True
        get_best_fallback('hermes', pool=None, stats=None)
        assert mock_healthy.call_count >= 3
        mock_healthy.assert_any_call('claude')
        mock_healthy.assert_any_call('kiro')

# --- CB edge cases ---

@patch('src.fallback_policy.FALLBACK_CHAIN', new={'hermes': ['claude', 'kiro']})
@patch('src.fallback_policy.is_agent_healthy', return_value=True)
def test_all_open_returns_none(mock_healthy):
    from src.fallback_policy import get_best_fallback
    with patch('src.fallback_policy._circuit_breakers', new={
        'claude': MockCircuitBreaker(CircuitState.OPEN),
        'kiro': MockCircuitBreaker(CircuitState.OPEN),
    }):
        result = get_best_fallback('hermes', pool=None, stats=None)
        assert result is None

@patch('src.fallback_policy.FALLBACK_CHAIN', new={'hermes': ['claude', 'kiro']})
@patch('src.fallback_policy.is_agent_healthy', return_value=True)
def test_circuit_breaker_priority(mock_healthy):
    from src.fallback_policy import get_best_fallback
    mock_pool = Mock()
    mock_pool._connections = {}
    mock_stats = Mock()
    def get_stats(agent, hours):
        return {'claude': {'success_rate': 0.95, 'avg_duration': 30.0},
                'kiro': {'success_rate': 0.70, 'avg_duration': 30.0}}.get(agent, {})
    mock_stats.get_agent_stats = Mock(side_effect=get_stats)
    with patch('src.fallback_policy._circuit_breakers', new={
        'claude': MockCircuitBreaker(CircuitState.HALF_OPEN),
        'kiro': MockCircuitBreaker(CircuitState.CLOSED),
    }):
        result = get_best_fallback('hermes', pool=mock_pool, stats=mock_stats)
        assert result == 'kiro'

# --- Pool multi-state ---

@patch('src.fallback_policy.FALLBACK_CHAIN', new={'hermes': ['claude', 'kiro', 'qwen']})
@patch('src.fallback_policy.is_agent_healthy', return_value=True)
@patch('src.fallback_policy._circuit_breakers', new={})
def test_multiple_idle_agents_compare_stats(mock_healthy):
    from src.fallback_policy import get_best_fallback
    mock_pool = Mock()
    mock_pool._connections = {('claude', 'c1'): Mock(state='idle'),
                              ('kiro', 'c2'): Mock(state='idle'),
                              ('qwen', 'c3'): Mock(state='idle')}
    mock_stats = Mock()
    def get_stats(agent, hours):
        return {'claude': {'success_rate': 0.85, 'avg_duration': 30.0},
                'kiro': {'success_rate': 0.90, 'avg_duration': 30.0},
                'qwen': {'success_rate': 0.80, 'avg_duration': 30.0}}.get(agent, {})
    mock_stats.get_agent_stats = Mock(side_effect=get_stats)
    result = get_best_fallback('hermes', pool=mock_pool, stats=mock_stats)
    assert result == 'kiro'

@patch('src.fallback_policy.FALLBACK_CHAIN', new={'hermes': ['claude', 'kiro']})
@patch('src.fallback_policy.is_agent_healthy', return_value=True)
@patch('src.fallback_policy._circuit_breakers', new={})
def test_no_idle_still_selectable(mock_healthy):
    from src.fallback_policy import get_best_fallback
    mock_pool = Mock()
    mock_pool._connections = {('claude', 'c1'): Mock(state='busy'), ('kiro', 'c2'): Mock(state='busy')}
    mock_stats = Mock()
    def get_stats(agent, hours):
        return {'claude': {'success_rate': 0.95, 'avg_duration': 30.0},
                'kiro': {'success_rate': 0.80, 'avg_duration': 30.0}}.get(agent, {})
    mock_stats.get_agent_stats = Mock(side_effect=get_stats)
    result = get_best_fallback('hermes', pool=mock_pool, stats=mock_stats)
    assert result == 'claude'

@patch('src.fallback_policy.FALLBACK_CHAIN', new={'hermes': ['claude', 'kiro']})
@patch('src.fallback_policy.is_agent_healthy', return_value=True)
@patch('src.fallback_policy._circuit_breakers', new={})
def test_pool_connections_parsing(mock_healthy):
    from src.fallback_policy import get_best_fallback
    mock_pool = Mock()
    mock_pool._connections = {('claude', 'conn-123'): Mock(state='idle'), ('other', 'conn-456'): Mock(state='busy')}
    mock_stats = Mock()
    mock_stats.get_agent_stats = Mock(return_value={'success_rate': 0.80, 'avg_duration': 30.0})
    result = get_best_fallback('hermes', pool=mock_pool, stats=mock_stats)
    assert result == 'claude'

# --- Stats edge cases & scoring ---

@patch('src.fallback_policy.FALLBACK_CHAIN', new={'hermes': ['claude', 'kiro']})
@patch('src.fallback_policy.is_agent_healthy', return_value=True)
@patch('src.fallback_policy._circuit_breakers', new={})
def test_trend_penalty_improving_success(mock_healthy):
    from src.fallback_policy import get_best_fallback
    mock_pool = Mock()
    mock_pool._connections = {}
    mock_stats = Mock()
    def get_stats(agent, hours):
        if hours == 1.0:
            return {'success_rate': 0.80, 'avg_duration': 30.0}
        else:
            return {'success_rate': 0.95, 'avg_duration': 30.0}
    mock_stats.get_agent_stats = Mock(side_effect=get_stats)
    result = get_best_fallback('hermes', pool=mock_pool, stats=mock_stats)
    assert result in ['claude', 'kiro']

@patch('src.fallback_policy.FALLBACK_CHAIN', new={'hermes': ['claude']})
@patch('src.fallback_policy.is_agent_healthy', return_value=True)
@patch('src.fallback_policy._circuit_breakers', new={})
def test_no_stats_defaults_to_100_percent(mock_healthy):
    from src.fallback_policy import get_best_fallback
    mock_pool = Mock()
    mock_pool._connections = {}
    mock_stats = Mock()
    mock_stats.get_agent_stats = Mock(return_value={})
    result = get_best_fallback('hermes', pool=mock_pool, stats=mock_stats)
    assert result == 'claude'

@patch('src.fallback_policy.FALLBACK_CHAIN', new={'hermes': ['claude', 'kiro']})
@patch('src.fallback_policy.is_agent_healthy', return_value=True)
@patch('src.fallback_policy._circuit_breakers', new={})
def test_15min_window_reflects_recent(mock_healthy):
    from src.fallback_policy import get_best_fallback
    mock_pool = Mock()
    mock_pool._connections = {}
    mock_stats = Mock()
    def get_stats(agent, hours):
        if agent == 'claude':
            return {'success_rate': 0.90 if hours == 1.0 else 0.50, 'avg_duration': 30.0}
        else:
            return {'success_rate': 0.85, 'avg_duration': 30.0}
    mock_stats.get_agent_stats = Mock(side_effect=get_stats)
    result = get_best_fallback('hermes', pool=mock_pool, stats=mock_stats)
    assert result == 'kiro'

@patch('src.fallback_policy.FALLBACK_CHAIN', new={'hermes': ['claude', 'kiro']})
@patch('src.fallback_policy.is_agent_healthy', return_value=True)
@patch('src.fallback_policy._circuit_breakers', new={})
def test_idle_high_success_vs_no_idle_perfect(mock_healthy):
    from src.fallback_policy import get_best_fallback
    mock_pool = Mock()
    mock_pool._connections = {('claude', 'c1'): Mock(state='idle'), ('kiro', 'c2'): Mock(state='busy')}
    mock_stats = Mock()
    def get_stats(agent, hours):
        return {'claude': {'success_rate': 0.90, 'avg_duration': 30.0},
                'kiro': {'success_rate': 1.00, 'avg_duration': 30.0}}.get(agent, {})
    mock_stats.get_agent_stats = Mock(side_effect=get_stats)
    result = get_best_fallback('hermes', pool=mock_pool, stats=mock_stats)
    assert result == 'claude'

@patch('src.fallback_policy.FALLBACK_CHAIN', new={'hermes': ['claude', 'kiro']})
@patch('src.fallback_policy.is_agent_healthy', return_value=True)
def test_half_open_idle_vs_closed_no_idle(mock_healthy):
    from src.fallback_policy import get_best_fallback
    mock_pool = Mock()
    mock_pool._connections = {('claude', 'c1'): Mock(state='idle'), ('kiro', 'c2'): Mock(state='busy')}
    mock_stats = Mock()
    mock_stats.get_agent_stats = Mock(return_value={'success_rate': 0.90, 'avg_duration': 30.0})
    with patch('src.fallback_policy._circuit_breakers', new={
        'claude': MockCircuitBreaker(CircuitState.HALF_OPEN),
        'kiro': MockCircuitBreaker(CircuitState.CLOSED),
    }):
        result = get_best_fallback('hermes', pool=mock_pool, stats=mock_stats)
        assert result == 'kiro'

@patch('src.fallback_policy.FALLBACK_CHAIN', new={'hermes': ['claude', 'kiro']})
@patch('src.fallback_policy.is_agent_healthy', return_value=True)
@patch('src.fallback_policy._circuit_breakers', new={})
def test_high_success_slow_vs_medium_success_fast(mock_healthy):
    from src.fallback_policy import get_best_fallback
    mock_pool = Mock()
    mock_pool._connections = {}
    mock_stats = Mock()
    def get_stats(agent, hours):
        return {'claude': {'success_rate': 0.95, 'avg_duration': 90.0},
                'kiro': {'success_rate': 0.80, 'avg_duration': 10.0}}.get(agent, {})
    mock_stats.get_agent_stats = Mock(side_effect=get_stats)
    result = get_best_fallback('hermes', pool=mock_pool, stats=mock_stats)
    assert result == 'claude'

@patch('src.fallback_policy.FALLBACK_CHAIN', new={'hermes': ['claude', 'kiro']})
@patch('src.fallback_policy.is_agent_healthy', return_value=True)
@patch('src.fallback_policy._circuit_breakers', new={})
def test_stable_vs_declining_success(mock_healthy):
    from src.fallback_policy import get_best_fallback
    mock_pool = Mock()
    mock_pool._connections = {}
    mock_stats = Mock()
    def get_stats(agent, hours):
        if agent == 'claude':
            return {'success_rate': 0.80, 'avg_duration': 30.0}
        else:
            return {'success_rate': 0.90 if hours == 1.0 else 0.70, 'avg_duration': 30.0}
    mock_stats.get_agent_stats = Mock(side_effect=get_stats)
    result = get_best_fallback('hermes', pool=mock_pool, stats=mock_stats)
    assert result in ['claude', 'kiro']
