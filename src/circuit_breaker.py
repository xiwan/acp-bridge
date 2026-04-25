"""Circuit breaker — fast-fail on unhealthy agents instead of waiting for timeout."""

import asyncio
import logging
import time
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Optional

log = logging.getLogger("acp-bridge.circuit_breaker")

try:
    from .metrics import metrics as _metrics
except Exception:
    _metrics = None


class CircuitState(Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreakerOpenError(Exception):
    """Raised when a call is rejected because the breaker is OPEN."""


@dataclass
class CircuitBreakerConfig:
    failure_threshold: int = 5
    failure_rate_threshold: float = 0.5
    window_size: int = 10
    open_timeout: float = 30.0
    half_open_max_calls: int = 3
    expected_exceptions: tuple = (Exception,)
    excluded_exceptions: tuple = ()  # subclasses to exclude from failure counting
    on_state_change: Optional[Callable] = None


class CircuitBreaker:
    """Per-agent circuit breaker with dual trigger (consecutive failures + failure rate)."""

    def __init__(self, name: str, config: CircuitBreakerConfig | None = None):
        self.name = name
        self.config = config or CircuitBreakerConfig()
        self.state = CircuitState.CLOSED
        self._results: deque[bool] = deque(maxlen=self.config.window_size)
        self._state_changed_at: float = time.monotonic()
        self._half_open_calls: int = 0
        self._lock = asyncio.Lock()
        # metrics
        self.total_calls: int = 0
        self.success_calls: int = 0
        self.failure_calls: int = 0
        self.circuit_open_count: int = 0
        self.last_failure_time: float | None = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def call(self, func: Callable, *args: Any, **kwargs: Any) -> Any:
        """Execute *func* through the breaker. Raises CircuitBreakerOpenError if OPEN."""
        async with self._lock:
            self._maybe_transition_to_half_open()
            if self.state == CircuitState.OPEN:
                raise CircuitBreakerOpenError(f"circuit breaker '{self.name}' is OPEN")
            if self.state == CircuitState.HALF_OPEN:
                if self._half_open_calls >= self.config.half_open_max_calls:
                    raise CircuitBreakerOpenError(
                        f"circuit breaker '{self.name}' half-open call limit reached")
                self._half_open_calls += 1

        try:
            result = await func(*args, **kwargs)
            await self._on_success()
            return result
        except self.config.excluded_exceptions:
            raise  # bypass breaker entirely (e.g. rate-limit)
        except self.config.expected_exceptions as e:
            await self._on_failure()
            raise

    def record_success(self) -> None:
        """Manual success recording (non-async path)."""
        self._results.append(True)
        self.total_calls += 1
        self.success_calls += 1
        if self.state == CircuitState.HALF_OPEN:
            self._set_state(CircuitState.CLOSED)
            self._half_open_calls = 0

    def record_failure(self) -> None:
        """Manual failure recording (non-async path)."""
        self._results.append(False)
        self.total_calls += 1
        self.failure_calls += 1
        self.last_failure_time = time.monotonic()
        if self.state == CircuitState.HALF_OPEN:
            self._set_state(CircuitState.OPEN)
            self._half_open_calls = 0
        elif self.state == CircuitState.CLOSED and self._should_open():
            self._set_state(CircuitState.OPEN)
            self._half_open_calls = 0

    def reset(self) -> None:
        """Force-reset to CLOSED."""
        self._results.clear()
        self._half_open_calls = 0
        self._set_state(CircuitState.CLOSED)

    def get_metrics(self) -> dict:
        n = len(self._results)
        failures = sum(1 for r in self._results if not r)
        return {
            "name": self.name,
            "state": self.state.value,
            "total_calls": self.total_calls,
            "success_calls": self.success_calls,
            "failure_calls": self.failure_calls,
            "circuit_open_count": self.circuit_open_count,
            "failure_rate": failures / n if n else 0.0,
            "window_size": n,
        }

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _on_success(self) -> None:
        async with self._lock:
            self.record_success()

    async def _on_failure(self) -> None:
        async with self._lock:
            self.record_failure()

    def _should_open(self) -> bool:
        # Trigger 1: consecutive failures
        consecutive = 0
        for r in reversed(self._results):
            if not r:
                consecutive += 1
            else:
                break
        if consecutive >= self.config.failure_threshold:
            return True
        # Trigger 2: failure rate over full window
        if len(self._results) >= self.config.window_size:
            rate = sum(1 for r in self._results if not r) / len(self._results)
            if rate >= self.config.failure_rate_threshold:
                return True
        return False

    def _maybe_transition_to_half_open(self) -> None:
        if self.state != CircuitState.OPEN:
            return
        elapsed = time.monotonic() - self._state_changed_at
        if elapsed >= self.config.open_timeout:
            self._set_state(CircuitState.HALF_OPEN)
            self._half_open_calls = 0

    def _set_state(self, new: CircuitState) -> None:
        old = self.state
        if old == new:
            return
        self.state = new
        self._state_changed_at = time.monotonic()
        if new == CircuitState.OPEN:
            self.circuit_open_count += 1
        log.info("circuit_breaker: %s %s -> %s", self.name, old.value, new.value)
        try:
            if _metrics is not None:
                _metrics.set_circuit_breaker_state(self.name, new.value)
        except Exception:
            pass
        if self.config.on_state_change:
            self.config.on_state_change(self.name, old, new)
