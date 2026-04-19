"""Request trace_id middleware + logging filter.

Generates/accepts X-Request-Id header, stores in contextvars so all async log
records under the request share the trace_id. Returned via response header.
"""

from __future__ import annotations

import contextvars
import logging
import uuid

from starlette.middleware.base import BaseHTTPMiddleware

_trace_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("trace_id", default="-")


def current_trace_id() -> str:
    return _trace_id_var.get()


class TraceIdFilter(logging.Filter):
    """Inject current trace_id into every LogRecord as record.trace_id."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.trace_id = _trace_id_var.get()
        return True


class TraceIdMiddleware(BaseHTTPMiddleware):
    """Generate or accept X-Request-Id; expose in response header."""

    async def dispatch(self, request, call_next):
        tid = request.headers.get("x-request-id") or uuid.uuid4().hex[:12]
        token = _trace_id_var.set(tid)
        try:
            response = await call_next(request)
            response.headers["x-request-id"] = tid
            return response
        finally:
            _trace_id_var.reset(token)
