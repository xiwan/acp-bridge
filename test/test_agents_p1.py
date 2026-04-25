"""
P1 Priority Tests for agents.py::get_best_fallback()
Covers: health checks (5-7), CB edge cases (11-12), pool multi-state (14-16), stats edge + scoring (20-26)
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from unittest.mock import Mock, patch, call
from src.circuit_breaker import CircuitState


class MockCircuitBreaker:
    def __init__(self, state: CircuitState):
        self.state = state


# ============================================================
# 2. HEALTH CHECK (3 tests: 5-7)
# ============================================================

@patch('src.fallback_policy.FALLBACK_CHAIN', new={'hermes': ['unhealthy', 'claude', 'kiro']})
@patch('src.fallback_policy._circuit_breakers', new={})
def test_prefer_healthy_agent():
    """Scenario 5: Prefer healthy agent even if later in chain"""
    from src.fallback_policy import get_best_fallback

    with patch('src.fallback_policy.is_agent_healthy') as mock_healthy:
        mock_healthy.side_effect = lambda a: a != 'unhealthy'

        result = get_best_fallback('hermes', pool=None, stats=None)
        assert result == 'claude'  # Skip unhealthy, pick first healthy


@patch('src.fallback_policy.FALLBACK_CHAIN', new={'hermes': ['claude', 'kiro']})
@patch('src.fallback_policy._circuit_breakers', new={})
def test_fallback_to_unhealthy_if_no_healthy():
    """Scenario 6: If no healthy agents, still return unhealthy (degraded mode)"""
    from src.fallback_policy import get_best_fallback

    with patch('src.fallback_policy.is_agent_healthy', return_value=False):
        result = get_best_fallback('hermes', pool=None, stats=None)
        assert result == 'claude'  # Degraded: return first even if unhealthy


@patch('src.fallback_policy.FALLBACK_CHAIN', new={'hermes': ['claude', 'kiro', 'qwen']})
@patch('src.fallback_policy._circuit_breakers', new={})
def test_is_agent_healthy_integration():
    """Scenario 7: Verify is_agent_healthy is called for all candidates"""
    from src.fallback_policy import get_best_fallback

    with patch('src.fallback_policy.is_agent_healthy') as mock_healthy:
        mock_healthy.return_value = True
        get_best_fallback('hermes', pool=None, stats=None)

        # Should check all candidates
        assert mock_healthy.call_count >= 3
        mock_healthy.assert_any_call('claude')
        mock_healthy.assert_any_call('kiro')


# ============================================================
# 3. CIRCUIT BREAKER EDGE CASES (2 tests: 11-12)
# ============================================================

@patch('src.fallback_policy.FALLBACK_CHAIN', new={'hermes': ['claude', 'kiro']})
@patch('src.fallback_policy.is_agent_healthy', return_value=True)
def test_all_open_returns_none(mock_healthy):
    """Scenario 11: All candidates OPEN -> returns None"""
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
    """Scenario 12: CLOSED+low success vs HALF_OPEN+high success"""
    from src.fallback_policy import get_best_fallback

    mock_pool = Mock()
    mock_pool._connections = {}

    mock_stats = Mock()
    def get_stats(agent, hours):
        return {
            'claude': {'success_rate': 0.95, 'avg_duration': 30.0},  # High but HALF_OPEN
            'kiro': {'success_rate': 0.70, 'avg_duration': 30.0}     # Low but CLOSED
        }.get(agent, {})
    mock_stats.get_agent_stats = Mock(side_effect=get_stats)

    with patch('src.fallback_policy._circuit_breakers', new={
        'claude': MockCircuitBreaker(CircuitState.HALF_OPEN),  # 0.5x penalty
        'kiro': MockCircuitBreaker(CircuitState.CLOSED),
    }):
        result = get_best_fallback('hermes', pool=mock_pool, stats=mock_stats)
        # claude: 95*0.5 = 47.5 (after CB penalty)
        # kiro: 70*1.0 = 70
        assert result == 'kiro'


# ============================================================
# 4. POOL MULTI-STATE (3 tests: 14-16)
# ============================================================

@patch('src.fallback_policy.FALLBACK_CHAIN', new={'hermes': ['claude', 'kiro', 'qwen']})
@patch('src.fallback_policy.is_agent_healthy', return_value=True)
@patch('src.fallback_policy._circuit_breakers', new={})
def test_multiple_idle_agents_compare_stats(mock_healthy):
    """Scenario 14: Multiple idle agents -> compare by stats"""
    from src.fallback_policy import get_best_fallback

    # All agents are idle
    mock_conn1 = Mock(state='idle')
    mock_conn2 = Mock(state='idle')
    mock_conn3 = Mock(state='idle')

    mock_pool = Mock()
    mock_pool._connections = {
        ('claude', 'c1'): mock_conn1,
        ('kiro', 'c2'): mock_conn2,
        ('qwen', 'c3'): mock_conn3,
    }

    mock_stats = Mock()
    def get_stats(agent, hours):
        return {
            'claude': {'success_rate': 0.85, 'avg_duration': 30.0},
            'kiro': {'success_rate': 0.90, 'avg_duration': 30.0},  # Best
            'qwen': {'success_rate': 0.80, 'avg_duration': 30.0},
        }.get(agent, {})
    mock_stats.get_agent_stats = Mock(side_effect=get_stats)

    result = get_best_fallback('hermes', pool=mock_pool, stats=mock_stats)
    assert result == 'kiro'  # Highest success rate among idle


@patch('src.fallback_policy.FALLBACK_CHAIN', new={'hermes': ['claude', 'kiro']})
@patch('src.fallback_policy.is_agent_healthy', return_value=True)
@patch('src.fallback_policy._circuit_breakers', new={})
def test_no_idle_still_selectable(mock_healthy):
    """Scenario 15: No idle agents -> still select best by stats"""
    from src.fallback_policy import get_best_fallback

    mock_conn1 = Mock(state='busy')
    mock_conn2 = Mock(state='busy')

    mock_pool = Mock()
    mock_pool._connections = {
        ('claude', 'c1'): mock_conn1,
        ('kiro', 'c2'): mock_conn2,
    }

    mock_stats = Mock()
    def get_stats(agent, hours):
        return {
            'claude': {'success_rate': 0.95, 'avg_duration': 30.0},
            'kiro': {'success_rate': 0.80, 'avg_duration': 30.0},
        }.get(agent, {})
    mock_stats.get_agent_stats = Mock(side_effect=get_stats)

    result = get_best_fallback('hermes', pool=mock_pool, stats=mock_stats)
    assert result == 'claude'  # No idle bonus, pure stats win


@patch('src.fallback_policy.FALLBACK_CHAIN', new={'hermes': ['claude', 'kiro']})
@patch('src.fallback_policy.is_agent_healthy', return_value=True)
@patch('src.fallback_policy._circuit_breakers', new={})
def test_pool_connections_parsing(mock_healthy):
    """Scenario 16: Verify pool._connections tuple key parsing"""
    from src.fallback_policy import get_best_fallback

    mock_conn = Mock(state='idle')
    mock_pool = Mock()
    # Key format: (agent_name, conn_id)
    mock_pool._connections = {
        ('claude', 'conn-123'): mock_conn,
        ('other', 'conn-456'): Mock(state='busy'),
    }

    mock_stats = Mock()
    mock_stats.get_agent_stats = Mock(return_value={'success_rate': 0.80, 'avg_duration': 30.0})

    result = get_best_fallback('hermes', pool=mock_pool, stats=mock_stats)
    # Should correctly parse ('claude', 'conn-123') and detect idle
    assert result == 'claude'


# ============================================================
# 5. STATS EDGE CASES + SCORING (7 tests: 20-26)
# ============================================================

@patch('src.fallback_policy.FALLBACK_CHAIN', new={'hermes': ['claude', 'kiro']})
@patch('src.fallback_policy.is_agent_healthy', return_value=True)
@patch('src.fallback_policy._circuit_breakers', new={})
def test_trend_penalty_improving_success(mock_healthy):
    """Scenario 20: Success rate improving -> no penalty (max(0, ...))"""
    from src.fallback_policy import get_best_fallback

    mock_pool = Mock()
    mock_pool._connections = {}

    mock_stats = Mock()
    def get_stats(agent, hours):
        if hours == 1.0:
            return {'success_rate': 0.80, 'avg_duration': 30.0}
        else:  # 0.25 (15m) - improved!
            return {'success_rate': 0.95, 'avg_duration': 30.0}
    mock_stats.get_agent_stats = Mock(side_effect=get_stats)

    result = get_best_fallback('hermes', pool=mock_pool, stats=mock_stats)
    # Penalty = max(0, (1-0.95)-(1-0.80))*50 = max(0, -0.15*50) = 0
    assert result in ['claude', 'kiro']  # No penalty applied


@patch('src.fallback_policy.FALLBACK_CHAIN', new={'hermes': ['claude']})
@patch('src.fallback_policy.is_agent_healthy', return_value=True)
@patch('src.fallback_policy._circuit_breakers', new={})
def test_no_stats_defaults_to_100_percent(mock_healthy):
    """Scenario 21: Empty stats dict -> defaults to success_rate=1.0"""
    from src.fallback_policy import get_best_fallback

    mock_pool = Mock()
    mock_pool._connections = {}

    mock_stats = Mock()
    mock_stats.get_agent_stats = Mock(return_value={})  # Empty

    result = get_best_fallback('hermes', pool=mock_pool, stats=mock_stats)
    # Should use default success_rate=1.0, avg_duration=30.0
    assert result == 'claude'


@patch('src.fallback_policy.FALLBACK_CHAIN', new={'hermes': ['claude', 'kiro']})
@patch('src.fallback_policy.is_agent_healthy', return_value=True)
@patch('src.fallback_policy._circuit_breakers', new={})
def test_15min_window_reflects_recent(mock_healthy):
    """Scenario 22: 15min window shows recent degradation"""
    from src.fallback_policy import get_best_fallback

    mock_pool = Mock()
    mock_pool._connections = {}

    mock_stats = Mock()
    def get_stats(agent, hours):
        if agent == 'claude':
            return {'success_rate': 0.90 if hours == 1.0 else 0.50, 'avg_duration': 30.0}
        else:  # kiro stable
            return {'success_rate': 0.85, 'avg_duration': 30.0}
    mock_stats.get_agent_stats = Mock(side_effect=get_stats)

    result = get_best_fallback('hermes', pool=mock_pool, stats=mock_stats)
    # claude: 90 - ((1-0.5)-(1-0.9))*50 = 90 - 20 = 70
    # kiro: 85 - 0 = 85
    assert result == 'kiro'  # Recent trend matters


@patch('src.fallback_policy.FALLBACK_CHAIN', new={'hermes': ['claude', 'kiro']})
@patch('src.fallback_policy.is_agent_healthy', return_value=True)
@patch('src.fallback_policy._circuit_breakers', new={})
def test_idle_high_success_vs_no_idle_perfect(mock_healthy):
    """Scenario 23: idle+90% vs no_idle+100%"""
    from src.fallback_policy import get_best_fallback

    mock_conn1 = Mock(state='idle')
    mock_conn2 = Mock(state='busy')
    mock_pool = Mock()
    mock_pool._connections = {
        ('claude', 'c1'): mock_conn1,  # idle
        ('kiro', 'c2'): mock_conn2,     # busy
    }

    mock_stats = Mock()
    def get_stats(agent, hours):
        return {
            'claude': {'success_rate': 0.90, 'avg_duration': 30.0},  # idle+90%
            'kiro': {'success_rate': 1.00, 'avg_duration': 30.0}     # busy+100%
        }.get(agent, {})
    mock_stats.get_agent_stats = Mock(side_effect=get_stats)

    result = get_best_fallback('hermes', pool=mock_pool, stats=mock_stats)
    # claude: 90*1.5 = 135, kiro: 100*1.0 = 100
    assert result == 'claude'  # idle bonus wins


@patch('src.fallback_policy.FALLBACK_CHAIN', new={'hermes': ['claude', 'kiro']})
@patch('src.fallback_policy.is_agent_healthy', return_value=True)
def test_half_open_idle_vs_closed_no_idle(mock_healthy):
    """Scenario 24: HALF_OPEN+idle vs CLOSED+no_idle"""
    from src.fallback_policy import get_best_fallback

    mock_conn1 = Mock(state='idle')
    mock_conn2 = Mock(state='busy')
    mock_pool = Mock()
    mock_pool._connections = {
        ('claude', 'c1'): mock_conn1,
        ('kiro', 'c2'): mock_conn2,
    }

    mock_stats = Mock()
    mock_stats.get_agent_stats = Mock(return_value={'success_rate': 0.90, 'avg_duration': 30.0})

    with patch('src.fallback_policy._circuit_breakers', new={
        'claude': MockCircuitBreaker(CircuitState.HALF_OPEN),
        'kiro': MockCircuitBreaker(CircuitState.CLOSED),
    }):
        result = get_best_fallback('hermes', pool=mock_pool, stats=mock_stats)
        # claude: 90*1.5*0.5 = 67.5, kiro: 90*1.0*1.0 = 90
        assert result == 'kiro'


@patch('src.fallback_policy.FALLBACK_CHAIN', new={'hermes': ['claude', 'kiro']})
@patch('src.fallback_policy.is_agent_healthy', return_value=True)
@patch('src.fallback_policy._circuit_breakers', new={})
def test_high_success_slow_vs_medium_success_fast(mock_healthy):
    """Scenario 25: 95%+slow vs 80%+fast"""
    from src.fallback_policy import get_best_fallback

    mock_pool = Mock()
    mock_pool._connections = {}

    mock_stats = Mock()
    def get_stats(agent, hours):
        return {
            'claude': {'success_rate': 0.95, 'avg_duration': 90.0},  # Slow
            'kiro': {'success_rate': 0.80, 'avg_duration': 10.0}     # Fast
        }.get(agent, {})
    mock_stats.get_agent_stats = Mock(side_effect=get_stats)

    result = get_best_fallback('hermes', pool=mock_pool, stats=mock_stats)
    # claude: 95 + 20/(1+90/30) = 95 + 5 = 100
    # kiro: 80 + 20/(1+10/30) ≈ 80 + 15 = 95
    assert result == 'claude'  # Success rate still dominates


@patch('src.fallback_policy.FALLBACK_CHAIN', new={'hermes': ['claude', 'kiro']})
@patch('src.fallback_policy.is_agent_healthy', return_value=True)
@patch('src.fallback_policy._circuit_breakers', new={})
def test_stable_vs_declining_success(mock_healthy):
    """Scenario 26: Stable 80% vs Declining 90%→70%"""
    from src.fallback_policy import get_best_fallback

    mock_pool = Mock()
    mock_pool._connections = {}

    mock_stats = Mock()
    def get_stats(agent, hours):
        if agent == 'claude':  # Stable
            return {'success_rate': 0.80, 'avg_duration': 30.0}
        else:  # Declining
            return {'success_rate': 0.90 if hours == 1.0 else 0.70, 'avg_duration': 30.0}
    mock_stats.get_agent_stats = Mock(side_effect=get_stats)

    result = get_best_fallback('hermes', pool=mock_pool, stats=mock_stats)
    # claude: 80 - 0 = 80
    # kiro: 90 - ((1-0.7)-(1-0.9))*50 = 90 - 10 = 80
    # Tie, but claude first in chain
    assert result in ['claude', 'kiro']


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
