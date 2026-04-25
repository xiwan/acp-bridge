"""Fallback policy — chain config, agent selection, scoring, health state."""

import logging
import threading
from typing import Any, Optional

from .circuit_breaker import CircuitBreaker, CircuitState
from .stats import StatsCollector

log = logging.getLogger("acp-bridge.fallback_policy")

# Lock protecting _agent_healthy and _circuit_breakers
_state_lock = threading.RLock()

# =============================================================================
# Fallback chain configuration
# =============================================================================

_DEFAULT_FALLBACK_CHAIN = {
    "kiro": ["claude", "opencode", "qwen"],
    "claude": ["opencode", "qwen", "kiro"],
    "opencode": ["kiro", "claude", "qwen"],
    "qwen": ["claude", "kiro", "opencode"],
    "hermes": ["claude", "kiro", "opencode"],
}

FALLBACK_CHAIN: dict[str, list[str]] = dict(_DEFAULT_FALLBACK_CHAIN)

_fallback_chain_path: str = ""


def load_fallback_chain(path: str) -> None:
    """Load FALLBACK_CHAIN from YAML. Falls back to defaults if file missing."""
    global _fallback_chain_path
    _fallback_chain_path = path
    try:
        import yaml
        with open(path) as f:
            data = yaml.safe_load(f)
        if isinstance(data, dict):
            FALLBACK_CHAIN.clear()
            FALLBACK_CHAIN.update(data)
            log.info("fallback_chain_loaded: path=%s agents=%s", path, list(data.keys()))
        else:
            log.warning("fallback_chain_invalid: path=%s, using defaults", path)
    except FileNotFoundError:
        log.info("fallback_chain_default: %s not found, using built-in defaults", path)
    except Exception as e:
        log.warning("fallback_chain_load_error: %s, using defaults: %s", path, e)


def save_fallback_chain() -> None:
    """Persist current FALLBACK_CHAIN to YAML."""
    if not _fallback_chain_path:
        return
    import yaml
    from pathlib import Path
    Path(_fallback_chain_path).parent.mkdir(parents=True, exist_ok=True)
    with open(_fallback_chain_path, "w") as f:
        yaml.dump(dict(FALLBACK_CHAIN), f, default_flow_style=False, allow_unicode=True)
    log.info("fallback_chain_saved: path=%s", _fallback_chain_path)


# =============================================================================
# Per-agent health & circuit breaker state
# =============================================================================

# Per-agent health state (default True = optimistic on cold start)
_agent_healthy: dict[str, bool] = {}

# Per-agent circuit breakers
_circuit_breakers: dict[str, CircuitBreaker] = {}


def is_agent_healthy(agent: str) -> bool:
    with _state_lock:
        return _agent_healthy.get(agent, True)


# =============================================================================
# Fallback selection
# =============================================================================

def get_next_fallback(failed_agent: str, tried_agents: list[str] | None = None) -> Optional[str]:
    """Get the next agent in the fallback chain (static order, no pool/stats needed)."""
    if tried_agents is None:
        tried_agents = []

    candidates = FALLBACK_CHAIN.get(failed_agent, [])
    for agent in candidates:
        if agent not in tried_agents:
            return agent
    return None


def get_best_fallback(
    failed_agent: str,
    tried_agents: list[str] | None = None,
    pool: Any = None,
    stats: StatsCollector | None = None,
) -> Optional[str]:
    """Pick the best fallback agent using live pool state + historical stats + circuit breaker state.

    Args:
        pool: Any object with a ``_connections`` dict (duck typing to avoid circular imports).

    Scoring (higher = better):
      1. has_idle  — agent has at least one idle connection (bool, prioritized)
      2. success_rate — recent success ratio (0.0–1.0)
      3. -avg_duration — prefer faster agents

    Agents with OPEN circuit breakers are filtered out from candidates.
    Falls back to static chain order when pool/stats are unavailable.
    """
    if tried_agents is None:
        tried_agents = []

    with _state_lock:
        candidates = [a for a in FALLBACK_CHAIN.get(failed_agent, [])
                      if a not in tried_agents and is_agent_healthy(a)]
        if not candidates:
            candidates = [a for a in FALLBACK_CHAIN.get(failed_agent, []) if a not in tried_agents]
        if not candidates:
            return None

        # Filter out agents with OPEN circuit breakers
        open_breakers = []
        for agent in candidates:
            breaker = _circuit_breakers.get(agent)
            if breaker and breaker.state == CircuitState.OPEN:
                open_breakers.append(agent)

        if open_breakers:
            log.debug("fallback_filtered_open_breakers: agents=%s", open_breakers)
            candidates = [a for a in candidates if a not in open_breakers]
            if not candidates:
                return None

        # Snapshot CB states for scoring (avoid holding lock during stats queries)
        cb_states = {}
        for agent in candidates:
            breaker = _circuit_breakers.get(agent)
            cb_states[agent] = breaker.state if breaker else CircuitState.CLOSED

    # No runtime data → static order
    if not pool and not stats:
        return candidates[0]

    # Pre-fetch stats for all candidates (P1 fix: avoid repeated queries in score())
    stats_cache: dict[str, tuple[dict, dict]] = {}
    if stats:
        for agent in candidates:
            stats_cache[agent] = (
                stats.get_agent_stats(agent, hours=1.0),
                stats.get_agent_stats(agent, hours=0.25),
            )

    def score(agent: str) -> float:
        cb_weight = 0.5 if cb_states[agent] == CircuitState.HALF_OPEN else 1.0

        has_idle = False
        if pool:
            try:
                for (a, _), conn in list(pool._connections.items()):
                    if a == agent and conn.state == "idle":
                        has_idle = True
                        break
            except RuntimeError:
                pass

        s_1h, s_15m = stats_cache.get(agent, ({}, {}))
        rate_1h = s_1h.get("success_rate", 1.0)
        rate_15m = s_15m.get("success_rate", 1.0)
        avg_dur = s_1h.get("avg_duration", 30.0)

        base = 100 * rate_1h + 20 / (1 + avg_dur / 30)
        trend_penalty = max(0, (1 - rate_15m) - (1 - rate_1h)) * 50
        val = (base - trend_penalty) * (1.5 if has_idle else 1.0) * cb_weight
        log.debug("fallback_score: agent=%s score=%.1f (idle=%s rate_1h=%.2f rate_15m=%.2f dur=%.1f penalty=%.1f cb_weight=%.1f)",
                  agent, val, has_idle, rate_1h, rate_15m, avg_dur, trend_penalty, cb_weight)
        return val

    candidates.sort(key=score, reverse=True)
    best = candidates[0]
    log.info("fallback_decision: failed=%s tried=%s best=%s scores=%s",
              failed_agent, tried_agents, best,
              {a: score(a) for a in candidates[:3]})
    return best
