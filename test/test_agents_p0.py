"""
P0 Priority Tests for agents.py::get_best_fallback()

Covers: basic scenarios (1-4), circuit breaker filtering (8-10), pool state (13), stats scoring (17-19)
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from unittest.mock import Mock, patch
from src.circuit_breaker import CircuitState


# Mock CircuitBreaker class
class MockCircuitBreaker:
    def __init__(self, state: CircuitState):
        self.state = state


# ============================================================
# 1. BASIC SCENARIOS (4 tests)
# ============================================================

@patch('src.fallback_policy.FALLBACK_CHAIN', {})
@patch('src.fallback_policy.is_agent_healthy', return_value=True)
@patch('src.fallback_policy._circuit_breakers', new={})
def test_no_candidates_empty_fallback_chain(mock_healthy):
    """Scenario 1: FALLBACK_CHAIN has no entry for failed_agent -> returns None"""
    from src.fallback_policy import get_best_fallback
    result = get_best_fallback('hermes')
    assert result is None


@patch('src.fallback_policy.FALLBACK_CHAIN', {'hermes': ['claude', 'kiro']})
@patch('src.fallback_policy.is_agent_healthy', return_value=True)
@patch('src.fallback_policy._circuit_breakers', new={})
def test_no_candidates_all_tried(mock_healthy):
    """Scenario 2: All candidates already in tried_agents -> returns None"""
    from src.fallback_policy import get_best_fallback
    result = get_best_fallback('hermes', tried_agents=['claude', 'kiro'])
    assert result is None


@patch('src.fallback_policy.FALLBACK_CHAIN', {'hermes': ['claude']})
@patch('src.fallback_policy.is_agent_healthy', return_value=True)
@patch('src.fallback_policy._circuit_breakers', new={})
def test_single_candidate(mock_healthy):
    """Scenario 3: Only one candidate available -> returns it immediately"""
    from src.fallback_policy import get_best_fallback
    result = get_best_fallback('hermes')
    assert result == 'claude'


@patch('src.fallback_policy.FALLBACK_CHAIN', {'hermes': ['claude', 'kiro', 'qwen']})
@patch('src.fallback_policy.is_agent_healthy', return_value=True)
@patch('src.fallback_policy._circuit_breakers', new={})
def test_no_pool_no_stats_returns_first(mock_healthy):
    """Scenario 4: Without pool/stats -> returns first candidate (static fallback)"""
    from src.fallback_policy import get_best_fallback
    result = get_best_fallback('hermes', pool=None, stats=None)
    assert result == 'claude'


# ============================================================
# 3. CIRCUIT BREAKER INTEGRATION (3 tests: 8-10)
# ============================================================

@patch('src.fallback_policy.FALLBACK_CHAIN', {'hermes': ['claude', 'kiro', 'qwen']})
@patch('src.fallback_policy.is_agent_healthy', return_value=True)
def test_filter_open_circuit_breakers(mock_healthy):
    """Scenario 8: OPEN circuit breakers are filtered out"""
    from src.fallback_policy import get_best_fallback

    with patch('src.fallback_policy._circuit_breakers', {
        'claude': MockCircuitBreaker(CircuitState.OPEN),
        'kiro': MockCircuitBreaker(CircuitState.CLOSED),
    }):
        # claude is OPEN, should pick kiro or qwen
        result = get_best_fallback('hermes', pool=None, stats=None)
        assert result in ['kiro', 'qwen']
        assert result != 'claude'


@patch('src.fallback_policy.FALLBACK_CHAIN', {'hermes': ['claude', 'kiro']})
@patch('src.fallback_policy.is_agent_healthy', return_value=True)
def test_half_open_penalty(mock_healthy):
    """Scenario 9: HALF_OPEN agents get 0.5x score multiplier"""
    from src.fallback_policy import get_best_fallback

    mock_pool = Mock()
    mock_pool._connections = {}

    mock_stats = Mock()
    mock_stats.get_agent_stats = Mock(return_value={
        'success_rate': 0.95,
        'avg_duration': 30.0
    })

    with patch('src.fallback_policy._circuit_breakers', {
        'claude': MockCircuitBreaker(CircuitState.HALF_OPEN),
        'kiro': MockCircuitBreaker(CircuitState.CLOSED),
    }):
        # Both have same stats, but kiro (CLOSED) should win due to cb_weight=1.0 vs 0.5
        result = get_best_fallback('hermes', pool=mock_pool, stats=mock_stats)
        assert result == 'kiro'


@patch('src.fallback_policy.FALLBACK_CHAIN', {'hermes': ['claude', 'kiro']})
@patch('src.fallback_policy.is_agent_healthy', return_value=True)
def test_closed_circuit_breaker_normal_score(mock_healthy):
    """Scenario 10: CLOSED circuit breakers use full score (cb_weight=1.0)"""
    from src.fallback_policy import get_best_fallback

    mock_pool = Mock()
    mock_pool._connections = {}

    mock_stats = Mock()
    def get_stats_side_effect(agent, hours):
        stats_map = {
            'claude': {'success_rate': 0.90, 'avg_duration': 20.0},
            'kiro': {'success_rate': 0.85, 'avg_duration': 25.0}
        }
        return stats_map.get(agent, {})

    mock_stats.get_agent_stats = Mock(side_effect=get_stats_side_effect)

    with patch('src.fallback_policy._circuit_breakers', {
        'claude': MockCircuitBreaker(CircuitState.CLOSED),
        'kiro': MockCircuitBreaker(CircuitState.CLOSED),
    }):
        result = get_best_fallback('hermes', pool=mock_pool, stats=mock_stats)
        assert result == 'claude'  # Higher success rate wins


# ============================================================
# 4. POOL STATE IMPACT (1 test: 13)
# ============================================================

@patch('src.fallback_policy.FALLBACK_CHAIN', {'hermes': ['claude', 'kiro']})
@patch('src.fallback_policy.is_agent_healthy', return_value=True)
@patch('src.fallback_policy._circuit_breakers', new={})
def test_has_idle_multiplier_1_5x(mock_healthy):
    """Scenario 13: idle agent gets 1.5x score multiplier"""
    from src.fallback_policy import get_best_fallback

    # Mock pool with claude=idle, kiro=busy
    mock_conn_claude = Mock()
    mock_conn_claude.state = 'idle'
    mock_conn_kiro = Mock()
    mock_conn_kiro.state = 'busy'

    mock_pool = Mock()
    mock_pool._connections = {
        ('claude', 'conn1'): mock_conn_claude,
        ('kiro', 'conn2'): mock_conn_kiro,
    }

    mock_stats = Mock()
    mock_stats.get_agent_stats = Mock(return_value={
        'success_rate': 0.80,  # Same stats for both
        'avg_duration': 30.0
    })

    result = get_best_fallback('hermes', pool=mock_pool, stats=mock_stats)
    assert result == 'claude'  # idle multiplier makes claude win


# ============================================================
# 5. STATS IMPACT (3 tests: 17-19)
# ============================================================

@patch('src.fallback_policy.FALLBACK_CHAIN', {'hermes': ['claude', 'kiro']})
@patch('src.fallback_policy.is_agent_healthy', return_value=True)
@patch('src.fallback_policy._circuit_breakers', new={})
def test_success_rate_1h_dominates(mock_healthy):
    """Scenario 17: 1h success_rate is the dominant scoring factor (100x weight)"""
    from src.fallback_policy import get_best_fallback

    mock_pool = Mock()
    mock_pool._connections = {}

    mock_stats = Mock()
    def get_stats(agent, hours):
        if hours == 1.0:
            return {
                'claude': {'success_rate': 0.95, 'avg_duration': 50.0},
                'kiro': {'success_rate': 0.70, 'avg_duration': 10.0}
            }.get(agent, {})
        else:  # 0.25 (15m)
            return {'success_rate': 0.95 if agent == 'claude' else 0.70, 'avg_duration': 30.0}

    mock_stats.get_agent_stats = Mock(side_effect=get_stats)

    result = get_best_fallback('hermes', pool=mock_pool, stats=mock_stats)
    # claude: 100*0.95 + 20/(1+50/30) ≈ 95 + 7.5 = 102.5
    # kiro: 100*0.70 + 20/(1+10/30) ≈ 70 + 15.4 = 85.4
    assert result == 'claude'


@patch('src.fallback_policy.FALLBACK_CHAIN', {'hermes': ['claude', 'kiro']})
@patch('src.fallback_policy.is_agent_healthy', return_value=True)
@patch('src.fallback_policy._circuit_breakers', new={})
def test_avg_duration_affects_score(mock_healthy):
    """Scenario 18: Lower avg_duration increases score (20/(1+dur/30))"""
    from src.fallback_policy import get_best_fallback

    mock_pool = Mock()
    mock_pool._connections = {}

    mock_stats = Mock()
    def get_stats(agent, hours):
        stats_map = {
            'claude': {'success_rate': 0.80, 'avg_duration': 10.0},  # Fast
            'kiro': {'success_rate': 0.80, 'avg_duration': 90.0}      # Slow
        }
        return stats_map.get(agent, {})

    mock_stats.get_agent_stats = Mock(side_effect=get_stats)

    result = get_best_fallback('hermes', pool=mock_pool, stats=mock_stats)
    # claude: 80 + 20/(1+10/30) ≈ 80 + 15 = 95
    # kiro: 80 + 20/(1+90/30) ≈ 80 + 5 = 85
    assert result == 'claude'


@patch('src.fallback_policy.FALLBACK_CHAIN', {'hermes': ['claude', 'kiro']})
@patch('src.fallback_policy.is_agent_healthy', return_value=True)
@patch('src.fallback_policy._circuit_breakers', new={})
def test_trend_penalty_declining_success(mock_healthy):
    """Scenario 19: 15m success decline triggers penalty (max 50 points)"""
    from src.fallback_policy import get_best_fallback

    mock_pool = Mock()
    mock_pool._connections = {}

    mock_stats = Mock()
    def get_stats(agent, hours):
        if hours == 1.0:  # 1h window
            return {
                'claude': {'success_rate': 0.90, 'avg_duration': 30.0},  # Stable
                'kiro': {'success_rate': 0.90, 'avg_duration': 30.0}     # Was good
            }.get(agent, {})
        else:  # 0.25 (15m window)
            return {
                'claude': {'success_rate': 0.90, 'avg_duration': 30.0},  # Still good
                'kiro': {'success_rate': 0.60, 'avg_duration': 30.0}     # Declining!
            }.get(agent, {})

    mock_stats.get_agent_stats = Mock(side_effect=get_stats)

    result = get_best_fallback('hermes', pool=mock_pool, stats=mock_stats)
    # claude: 90 + 20/2 - 0 = 100 (no trend penalty)
    # kiro: 90 + 20/2 - ((1-0.6)-(1-0.9))*50 = 100 - 15 = 85
    assert result == 'claude'


if __name__ == '__main__':
    pytest.main([__file__, '-v'])


# ============================================================
# 6. get_next_fallback (3 tests)
# ============================================================

@patch('src.fallback_policy.FALLBACK_CHAIN', {'claude': ['opencode', 'qwen', 'kiro']})
def test_get_next_fallback_skips_failed_agent():
    """failed='claude', tried=['claude'] → returns first untried in chain."""
    from src.fallback_policy import get_next_fallback
    result = get_next_fallback('claude', tried_agents=['claude'])
    assert result == 'opencode'


@patch('src.fallback_policy.FALLBACK_CHAIN', {'claude': ['opencode', 'qwen', 'kiro']})
def test_get_next_fallback_all_tried_returns_none():
    """All candidates in tried → returns None."""
    from src.fallback_policy import get_next_fallback
    result = get_next_fallback('claude', tried_agents=['opencode', 'qwen', 'kiro'])
    assert result is None


@patch('src.fallback_policy.FALLBACK_CHAIN', {'claude': ['opencode', 'qwen', 'kiro']})
def test_get_next_fallback_no_tried():
    """tried=[] → returns first candidate in chain."""
    from src.fallback_policy import get_next_fallback
    result = get_next_fallback('claude', tried_agents=[])
    assert result == 'opencode'


# ============================================================
# 7. strip_ansi (1 test)
# ============================================================

def test_strip_ansi_removes_escape_codes():
    """ANSI escape sequences are stripped, plain text preserved."""
    from src.agents import strip_ansi
    assert strip_ansi('\x1b[31mhello\x1b[0m') == 'hello'
