"""Lightweight observability layer — Prometheus optional, structured logging always."""

import logging
import time
from contextlib import contextmanager

log = logging.getLogger("acp-bridge.metrics")

try:
    from prometheus_client import Counter, Histogram, Gauge, start_http_server
    PROMETHEUS_AVAILABLE = True
except ImportError:
    PROMETHEUS_AVAILABLE = False


class MetricsCollector:
    def __init__(self):
        self._server_started = False
        if PROMETHEUS_AVAILABLE:
            self.agent_calls = Counter("agent_calls_total", "Total agent calls", ["agent", "status"])
            self.agent_duration = Histogram("agent_call_duration_seconds", "Agent call latency", ["agent"], buckets=[0.1, 0.5, 1, 2, 5, 10, 30, 60])
            self.fallback_triggered = Counter("fallback_triggered_total", "Fallback attempts", ["from_agent", "to_agent", "success"])
            self.fallback_exhausted = Counter("fallback_exhausted_total", "Fallback chain exhausted", ["agent"])
            self.fallback_duration = Histogram("fallback_duration_seconds", "Fallback decision latency", ["from_agent"])
            self.cb_state = Gauge("circuit_breaker_state", "CB state (0=closed,1=half_open,2=open)", ["agent"])
            self.cb_opened = Counter("circuit_breaker_opened_total", "CB opened count", ["agent"])
            self.pool_connections = Gauge("pool_connections", "Connections by state", ["agent", "state"])

    def _has(self, attr):
        return hasattr(self, attr)

    @contextmanager
    def track_operation(self, op, agent, **labels):
        start = time.time()
        status = "success"
        try:
            yield
        except Exception:
            status = "error"
            raise
        finally:
            duration = time.time() - start
            extra = " ".join(f"{k}={v}" for k, v in labels.items())
            log.info("%s: agent=%s status=%s duration=%.3fs %s", op, agent, status, duration, extra)
            if self._has("agent_calls") and op == "agent_call":
                self.agent_calls.labels(agent=agent, status=status).inc()
                self.agent_duration.labels(agent=agent).observe(duration)

    def record_fallback(self, from_agent, to_agent, success=True, duration=0.0):
        log.info("fallback: from=%s to=%s success=%s duration=%.3fs", from_agent, to_agent, success, duration)
        if self._has("fallback_triggered"):
            self.fallback_triggered.labels(from_agent=from_agent, to_agent=to_agent, success=str(success)).inc()
            if duration > 0:
                self.fallback_duration.labels(from_agent=from_agent).observe(duration)

    def record_fallback_exhausted(self, agent, tried_agents):
        log.warning("fallback_exhausted: agent=%s tried=%s", agent, tried_agents)
        if self._has("fallback_exhausted"):
            self.fallback_exhausted.labels(agent=agent).inc()

    def set_circuit_breaker_state(self, agent, state):
        state_map = {"closed": 0, "half_open": 1, "open": 2}
        log.info("cb_state: agent=%s state=%s", agent, state)
        if self._has("cb_state"):
            self.cb_state.labels(agent=agent).set(state_map.get(state, -1))
            if state == "open":
                self.cb_opened.labels(agent=agent).inc()

    def record_pool_state(self, agent, idle, busy):
        log.debug("pool_state: agent=%s idle=%d busy=%d", agent, idle, busy)
        if self._has("pool_connections"):
            self.pool_connections.labels(agent=agent, state="idle").set(idle)
            self.pool_connections.labels(agent=agent, state="busy").set(busy)

    def start_server(self, port=9090):
        """Start Prometheus metrics HTTP server (idempotent)."""
        if self._server_started:
            log.info("prometheus_server_already_running: port=%d", port)
            return
        if PROMETHEUS_AVAILABLE:
            start_http_server(port)
            self._server_started = True
            log.info("prometheus_server_started: port=%d", port)
        else:
            log.warning("prometheus not available, metrics server not started")


metrics = MetricsCollector()
