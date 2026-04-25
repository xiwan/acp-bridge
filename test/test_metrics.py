"""Tests for metrics.py — structured logging mode (no Prometheus dependency)."""

import logging
import pytest
from unittest.mock import patch

# Force PROMETHEUS_AVAILABLE=False before importing
with patch.dict("sys.modules", {"prometheus_client": None}):
    import importlib
    import src.metrics as _mod
    importlib.reload(_mod)
    from src.metrics import MetricsCollector


class TestMetricsCollector:

    def setup_method(self):
        self.m = MetricsCollector()

    def test_record_fallback_logs(self, caplog):
        with caplog.at_level(logging.INFO, logger="acp-bridge.metrics"):
            self.m.record_fallback("claude", "kiro", success=True)
        assert "from=claude" in caplog.text
        assert "to=kiro" in caplog.text

    def test_record_fallback_exhausted_logs(self, caplog):
        with caplog.at_level(logging.WARNING, logger="acp-bridge.metrics"):
            self.m.record_fallback_exhausted("claude", ["kiro", "qwen"])
        assert "fallback_exhausted" in caplog.text
        assert "claude" in caplog.text

    def test_set_circuit_breaker_state_logs(self, caplog):
        with caplog.at_level(logging.INFO, logger="acp-bridge.metrics"):
            self.m.set_circuit_breaker_state("kiro", "open")
        assert "cb_state" in caplog.text
        assert "open" in caplog.text

    def test_record_pool_state_logs(self, caplog):
        with caplog.at_level(logging.DEBUG, logger="acp-bridge.metrics"):
            self.m.record_pool_state("kiro", idle=2, busy=1)
        assert "pool_state" in caplog.text

    def test_track_operation_success(self, caplog):
        with caplog.at_level(logging.INFO, logger="acp-bridge.metrics"):
            with self.m.track_operation("agent_call", agent="claude"):
                pass
        assert "status=success" in caplog.text

    def test_track_operation_error(self, caplog):
        with caplog.at_level(logging.INFO, logger="acp-bridge.metrics"):
            with pytest.raises(ValueError):
                with self.m.track_operation("agent_call", agent="claude"):
                    raise ValueError("boom")
        assert "status=error" in caplog.text

    def test_track_operation_labels_included(self, caplog):
        """P1 fix: extra **labels appear in log output."""
        with caplog.at_level(logging.INFO, logger="acp-bridge.metrics"):
            with self.m.track_operation("agent_call", agent="claude", session="s1", attempt=2):
                pass
        assert "session=s1" in caplog.text
        assert "attempt=2" in caplog.text

    def test_record_fallback_with_duration(self, caplog):
        """P2 fix: duration is logged."""
        with caplog.at_level(logging.INFO, logger="acp-bridge.metrics"):
            self.m.record_fallback("claude", "kiro", duration=0.042)
        assert "0.042" in caplog.text

    def test_start_server_no_prometheus(self, caplog):
        """P0 fix: start_server exists and warns without prometheus."""
        with caplog.at_level(logging.WARNING, logger="acp-bridge.metrics"):
            self.m.start_server(9999)
        assert "not available" in caplog.text

    def test_no_prometheus_attrs(self):
        """Without prometheus_client, no Counter/Gauge/Histogram attrs."""
        assert not hasattr(self.m, "agent_calls")
        assert not hasattr(self.m, "cb_state")
