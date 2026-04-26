"""trace.py — Request tracing via ContextVar for acp-bridge.

Tracks every request's journey: which agents were tried, latencies,
fallback events, and outcomes. Uses ContextVar so trace state
propagates automatically through async call chains without threading issues.

Design (per claude review):
  - TraceSpan dataclass: type-safe, serializable
  - TraceContext: list of spans for one request (stored in ContextVar)
  - Helpers: start_span / finish_span / get_trace / clear_trace
  - parent_span_id: supports nested spans (router → fallback → executor)
"""

from __future__ import annotations

import time
import uuid
from contextvars import ContextVar
from dataclasses import asdict, dataclass, field
from typing import Any, Optional

# ---------------------------------------------------------------------------
# TraceSpan — one unit of work within a request
# ---------------------------------------------------------------------------

@dataclass
class TraceSpan:
    """Records a single operation within the request trace.

    Fields follow claude's recommended schema (HEARTBEAT review).
    """
    # Identity
    request_id: str
    span_id: str
    parent_span_id: Optional[str]
    operation: str          # "route" | "fallback" | "execute" | "complexity"

    # Agent info
    agent_name: Optional[str]       # None for router-level spans
    agent_protocol: Optional[str]   # "http" | "acp" | None

    # Timing
    start_time: float               # time.monotonic() at span start
    duration_ms: float = 0.0        # filled in by finish_span()

    # Outcome
    success: bool = False
    error_type: Optional[str] = None     # "timeout" | "circuit_open" | "http_500" | ...
    error_message: Optional[str] = None

    # Flexible extension (cb_state, retry_count, cost_usd, etc.)
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        """Serialize to dict for logging / Prometheus / JSON export."""
        return asdict(self)

    def finish(self, *, success: bool, error_type: Optional[str] = None,
               error_message: Optional[str] = None, **metadata_kwargs: Any) -> "TraceSpan":
        """Complete the span: record duration and outcome in-place."""
        self.duration_ms = (time.monotonic() - self.start_time) * 1000
        self.success = success
        self.error_type = error_type
        self.error_message = error_message
        if metadata_kwargs:
            self.metadata.update(metadata_kwargs)
        return self


# ---------------------------------------------------------------------------
# TraceContext — all spans for one request
# ---------------------------------------------------------------------------

@dataclass
class TraceContext:
    """Container for all spans generated during a single request."""
    request_id: str
    spans: list[TraceSpan] = field(default_factory=list)
    created_at: float = field(default_factory=time.monotonic)

    def add(self, span: TraceSpan) -> None:
        self.spans.append(span)

    def total_duration_ms(self) -> float:
        if not self.spans:
            return 0.0
        return sum(s.duration_ms for s in self.spans)

    def agents_tried(self) -> list[str]:
        """Ordered list of agents that were attempted."""
        return [s.agent_name for s in self.spans
                if s.agent_name and s.operation == "execute"]

    def fallback_count(self) -> int:
        """How many fallbacks occurred (failed execute spans)."""
        return sum(1 for s in self.spans
                   if s.operation == "execute" and not s.success)

    def to_summary(self) -> dict:
        """Compact summary for logging."""
        return {
            "request_id": self.request_id,
            "total_duration_ms": round(self.total_duration_ms(), 2),
            "agents_tried": self.agents_tried(),
            "fallback_count": self.fallback_count(),
            "span_count": len(self.spans),
            "success": any(s.success for s in self.spans if s.operation == "execute"),
        }

    def to_dict(self) -> dict:
        """Full serialization."""
        return {
            "request_id": self.request_id,
            "created_at": self.created_at,
            "spans": [s.to_dict() for s in self.spans],
            "summary": self.to_summary(),
        }


# ---------------------------------------------------------------------------
# ContextVar — propagates trace through async call chain
# ---------------------------------------------------------------------------

_trace_context: ContextVar[Optional[TraceContext]] = ContextVar(
    "acp_trace_context", default=None
)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def init_trace(request_id: Optional[str] = None) -> TraceContext:
    """Initialize a new TraceContext for the current async context.

    Call at the top of a request handler (HealthAwareRouter.route_with_timeout).
    Returns the new TraceContext.
    """
    rid = request_id or str(uuid.uuid4())
    ctx = TraceContext(request_id=rid)
    _trace_context.set(ctx)
    return ctx


def get_trace() -> Optional[TraceContext]:
    """Return the current TraceContext, or None if not initialized."""
    return _trace_context.get()


def clear_trace() -> None:
    """Remove trace context (useful in test teardown)."""
    _trace_context.set(None)


def start_span(
    operation: str,
    *,
    request_id: Optional[str] = None,
    parent_span_id: Optional[str] = None,
    agent_name: Optional[str] = None,
    agent_protocol: Optional[str] = None,
    **metadata_kwargs: Any,
) -> TraceSpan:
    """Create and register a new span in the current TraceContext.

    If no TraceContext exists, creates one automatically.
    Returns the span so the caller can finish it later.

    Usage:
        span = start_span("execute", agent_name="claude", agent_protocol="http")
        try:
            result = await do_something()
            span.finish(success=True)
        except Exception as e:
            span.finish(success=False, error_type="exception", error_message=str(e))
    """
    ctx = _trace_context.get()
    if ctx is None:
        # Auto-init if called without explicit init_trace()
        rid = request_id or "auto"
        ctx = TraceContext(request_id=rid)
        _trace_context.set(ctx)

    span = TraceSpan(
        request_id=ctx.request_id,
        span_id=str(uuid.uuid4()),
        parent_span_id=parent_span_id,
        operation=operation,
        agent_name=agent_name,
        agent_protocol=agent_protocol,
        start_time=time.monotonic(),
        metadata=dict(metadata_kwargs),
    )
    ctx.add(span)
    return span


def finish_span(
    span: TraceSpan,
    *,
    success: bool,
    error_type: Optional[str] = None,
    error_message: Optional[str] = None,
    **metadata_kwargs: Any,
) -> TraceSpan:
    """Convenience wrapper around span.finish()."""
    return span.finish(
        success=success,
        error_type=error_type,
        error_message=error_message,
        **metadata_kwargs,
    )
