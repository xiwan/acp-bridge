"""ACP Bridge Rate Limiter - Per-Agent RPM/Token Quota

Sliding-window rate limiter with per-agent RPM/TPM quotas.
When a quota is exceeded, returns the configured fallback agent name
so the caller can transparently re-route the request.

Config section in config.yaml:
    rate_limits:
      claude:
        rpm: 50
        tpm: 80000
        fallback: qwen
"""

from __future__ import annotations

import threading
from collections import deque
from dataclasses import dataclass, field
from time import time
from typing import Dict, Optional, Tuple

import yaml


@dataclass
class AgentQuota:
    rpm: int = 60               # max requests per minute
    tpm: int = 100_000          # max tokens per minute
    fallback: Optional[str] = None  # agent to fall back to when over quota


class RateLimiter:
    """
    Thread-safe sliding-window rate limiter.

    Each agent gets its own deque of (timestamp, tokens) records.
    Records older than 60 s are evicted on every check.
    """

    def __init__(self, config_path: str = "config.yaml"):
        self.quotas: Dict[str, AgentQuota] = {}
        self._windows: Dict[str, deque] = {}   # agent -> deque[(ts, tokens)]
        self._lock = threading.Lock()
        self._load_config(config_path)

    # ------------------------------------------------------------------
    # Config
    # ------------------------------------------------------------------

    def _load_config(self, path: str) -> None:
        try:
            with open(path) as f:
                cfg = yaml.safe_load(f) or {}
        except FileNotFoundError:
            cfg = {}

        for agent, limits in cfg.get("rate_limits", {}).items():
            self.quotas[agent] = AgentQuota(
                rpm=limits.get("rpm", 60),
                tpm=limits.get("tpm", 100_000),
                fallback=limits.get("fallback"),
            )
            self._windows[agent] = deque()

    def _detect_fallback_cycle(self, start: str) -> bool:
        """Return True if following fallback chain from *start* forms a cycle."""
        visited: set = set()
        current = start
        while current is not None:
            if current in visited:
                return True
            visited.add(current)
            quota = self.quotas.get(current)
            current = quota.fallback if quota else None
        return False

    def configure(self, agent: str, quota: AgentQuota) -> None:
        """Programmatically set a quota (useful for tests).

        Raises:
            ValueError: if the new quota would create a fallback cycle.
        """
        with self._lock:
            # Temporarily set to detect cycles
            old = self.quotas.get(agent)
            self.quotas[agent] = quota
            if self._detect_fallback_cycle(agent):
                # Rollback
                if old is None:
                    del self.quotas[agent]
                else:
                    self.quotas[agent] = old
                raise ValueError(f"Fallback cycle detected starting from '{agent}'")
            if agent not in self._windows:
                self._windows[agent] = deque()

    # ------------------------------------------------------------------
    # Core check
    # ------------------------------------------------------------------

    def check_and_consume(
        self, agent: str, estimated_tokens: int = 0
    ) -> Tuple[bool, Optional[str]]:
        """
        Check whether *agent* is within quota and, if so, record the call.

        Returns:
            (True, None)          – within quota, request recorded
            (False, fallback)     – over quota; caller should re-route to *fallback*
            (False, None)         – over quota and no fallback configured

        Raises:
            ValueError: if estimated_tokens is negative (would bypass TPM limit)
        """
        if estimated_tokens < 0:
            raise ValueError(f"estimated_tokens must be >= 0, got {estimated_tokens}")

        with self._lock:
            quota = self.quotas.get(agent)
            if quota is None:
                return (True, None)   # no limit configured → always allow

            # setdefault guards against a quota being added via configure()
            # after __init__ but before the first check for this agent
            window = self._windows.setdefault(agent, deque())
            now = time()

            # Evict records older than 60 s
            while window and now - window[0][0] > 60:
                window.popleft()

            rpm_used = len(window)
            tpm_used = sum(tok for _, tok in window)

            if rpm_used >= quota.rpm or (tpm_used + estimated_tokens) >= quota.tpm:
                return (False, quota.fallback)

            # Consume quota
            window.append((now, estimated_tokens))
            return (True, None)

    def rollback(self, agent: str, tokens: int) -> bool:
        """Remove the last record if it matches *tokens* and is within 1 s."""
        with self._lock:
            window = self._windows.get(agent)
            if not window:
                return False
            ts, tok = window[-1]
            if tok == tokens and (time() - ts) <= 1.0:
                window.pop()
                return True
            return False

    # ------------------------------------------------------------------
    # Observability
    # ------------------------------------------------------------------

    def get_stats(self, agent: str) -> Dict:
        """Return current-window usage counters for *agent*."""
        quota = self.quotas.get(agent, AgentQuota())
        with self._lock:
            window = self._windows.get(agent, deque())
            now = time()
            recent = [(ts, tok) for ts, tok in window if now - ts <= 60]
        return {
            "rpm_used": len(recent),
            "tpm_used": sum(tok for _, tok in recent),
            "rpm_limit": quota.rpm,
            "tpm_limit": quota.tpm,
            "fallback": quota.fallback,
        }

    def all_stats(self) -> Dict[str, Dict]:
        """Return stats for every configured agent."""
        return {agent: self.get_stats(agent) for agent in self.quotas}
