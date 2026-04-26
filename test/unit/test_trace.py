"""Unit tests for src/trace.py

Tests the ContextVar-based distributed tracing middleware:
- TraceIdMiddleware: generates/accepts X-Request-Id header
- TraceIdFilter: injects trace_id into log records
- current_trace_id(): returns the current request trace ID
- Async isolation: concurrent coroutines get independent trace IDs
"""

import asyncio
import logging
import uuid
import pytest
from unittest.mock import AsyncMock, MagicMock
from starlette.testclient import TestClient
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route
from starlette.requests import Request

from src.trace import TraceIdMiddleware, TraceIdFilter, current_trace_id


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_app():
    """Create a minimal Starlette app with TraceIdMiddleware."""
    async def homepage(request: Request):
        # Echo the current trace_id in the body so tests can assert it
        tid = current_trace_id()
        return PlainTextResponse(tid)

    app = Starlette(routes=[Route("/", homepage)])
    app.add_middleware(TraceIdMiddleware)
    return app


# ---------------------------------------------------------------------------
# P0: TraceIdMiddleware — Basic Behavior
# ---------------------------------------------------------------------------

class TestTraceIdMiddleware:
    """Core middleware behavior."""

    def test_generates_trace_id_when_none_provided(self):
        """Middleware auto-generates a 12-char hex trace ID if no header given."""
        client = TestClient(make_app())
        resp = client.get("/")
        assert resp.status_code == 200
        tid = resp.headers["x-request-id"]
        assert len(tid) == 12
        assert all(c in "0123456789abcdef" for c in tid)

    def test_accepts_client_provided_trace_id(self):
        """When client sends X-Request-Id, middleware uses it as-is."""
        client = TestClient(make_app())
        custom_tid = "my-custom-trace-123"
        resp = client.get("/", headers={"x-request-id": custom_tid})
        assert resp.status_code == 200
        assert resp.headers["x-request-id"] == custom_tid
        # Handler can also see it via current_trace_id()
        assert resp.text == custom_tid

    def test_trace_id_propagated_to_handler(self):
        """current_trace_id() inside handler returns the same ID as the response header."""
        client = TestClient(make_app())
        resp = client.get("/")
        body_tid = resp.text
        header_tid = resp.headers["x-request-id"]
        assert body_tid == header_tid

    def test_trace_id_in_response_header(self):
        """Response always includes X-Request-Id header (for client correlation)."""
        client = TestClient(make_app())
        resp = client.get("/")
        assert "x-request-id" in resp.headers


# ---------------------------------------------------------------------------
# P0: Default State
# ---------------------------------------------------------------------------

class TestDefaultTraceId:
    """current_trace_id() outside a request context."""

    def test_default_returns_dash_outside_request(self):
        """Outside any request, trace_id defaults to '-' (not empty string)."""
        # ContextVar default is "-"
        tid = current_trace_id()
        assert tid == "-"


# ---------------------------------------------------------------------------
# P0: TraceIdFilter — Log Record Injection
# ---------------------------------------------------------------------------

class TestTraceIdFilter:
    """Logging filter injects trace_id into log records."""

    def test_filter_injects_trace_id_into_log_record(self):
        """TraceIdFilter.filter() adds record.trace_id with the current trace ID."""
        # Simulate being inside a request with a known trace_id
        from src.trace import _trace_id_var
        token = _trace_id_var.set("abc123def456")
        try:
            log_filter = TraceIdFilter()
            record = logging.LogRecord(
                name="test", level=logging.INFO, pathname="", lineno=0,
                msg="test message", args=(), exc_info=None
            )
            result = log_filter.filter(record)
            assert result is True  # filter should always pass
            assert record.trace_id == "abc123def456"
        finally:
            _trace_id_var.reset(token)

    def test_filter_uses_default_outside_request(self):
        """TraceIdFilter injects '-' when no trace ID is set."""
        log_filter = TraceIdFilter()
        record = logging.LogRecord(
            name="test", level=logging.INFO, pathname="", lineno=0,
            msg="test message", args=(), exc_info=None
        )
        log_filter.filter(record)
        assert record.trace_id == "-"


# ---------------------------------------------------------------------------
# P1: Async Isolation — Concurrent Coroutines Get Independent Trace IDs
# ---------------------------------------------------------------------------

class TestAsyncIsolation:
    """
    Critical: ContextVar (not threading.local) ensures that concurrent
    coroutines within the same thread each get their own trace_id.
    """

    @pytest.mark.asyncio
    async def test_concurrent_requests_have_independent_trace_ids(self):
        """Two concurrent requests don't share/overwrite each other's trace IDs."""
        from src.trace import _trace_id_var

        results = {}

        async def fake_request(name: str, tid: str):
            token = _trace_id_var.set(tid)
            try:
                # yield control to the event loop to simulate interleaving
                await asyncio.sleep(0)
                # After yielding, our trace_id should still be correct
                results[name] = current_trace_id()
            finally:
                _trace_id_var.reset(token)

        # Run two coroutines concurrently
        await asyncio.gather(
            fake_request("req_a", "aaaa11112222"),
            fake_request("req_b", "bbbb33334444"),
        )

        # Each should have seen its own trace_id, not the other's
        assert results["req_a"] == "aaaa11112222"
        assert results["req_b"] == "bbbb33334444"

    @pytest.mark.asyncio
    async def test_context_reset_after_request(self):
        """After a request completes, ContextVar is reset to default (no leakage)."""
        from src.trace import _trace_id_var

        # Set a trace ID
        token = _trace_id_var.set("leaky-trace-id")
        _trace_id_var.reset(token)

        # After reset, should be back to default
        assert current_trace_id() == "-"
