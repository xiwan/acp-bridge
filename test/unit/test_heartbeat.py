"""Unit tests for src/heartbeat.py — v0.23.1 diagnostic fidelity + fast-fail probe."""

import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.heartbeat import EnvCollector, HEARTBEAT_IDLE_TIMEOUT


def _collector():
    # No pool, minimal agents_cfg — refresh() tolerates pool=None
    return EnvCollector(None, {"kiro": {"enabled": True, "mode": "acp", "heartbeat": True}})


def test_fast_fail_timeout_is_short():
    # Probe must not inherit the 300s default — should fail fast.
    assert HEARTBEAT_IDLE_TIMEOUT <= 60


def test_record_keeps_real_response_when_silent():
    c = _collector()
    c.record("kiro", "prompt", "[SILENT]", silent=True, duration=3.2)
    h = c._history[-1]
    # Fix 1: real response retained in history regardless of silent flag.
    assert h["response"] == "[SILENT]"
    assert h["silent"] is True


def test_record_distinguishes_empty_from_silent():
    c = _collector()
    c.record("kiro", "prompt", "", silent=True, duration=300.0)   # timeout: empty
    c.record("kiro", "prompt", "[SILENT]", silent=True, duration=3.0)  # real reply
    empty, real = c._history[-2], c._history[-1]
    # Both silent, but response content tells them apart.
    assert empty["response"] == "" and empty["duration"] == 300.0
    assert real["response"] == "[SILENT]"
