"""Agent-level exceptions for smart retry routing."""

from .acp_client import AcpError


class AgentTimeoutError(AcpError):
    """Timeout → retry same agent in async mode."""


class AgentRateLimitError(AcpError):
    """Rate-limited → wait retry_after seconds then retry."""

    def __init__(self, message: str, retry_after: int = 60):
        super().__init__(message)
        self.retry_after = retry_after


class AgentModelError(AcpError):
    """Model/API error → skip to next fallback agent."""
