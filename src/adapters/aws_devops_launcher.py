#!/usr/bin/env python3
"""Fail-closed launcher for the AWS DevOps Agent sample ACP server.

The upstream sample automatically starts a paid/long-running investigation for
prompts that match an intent heuristic.  This launcher keeps normal ACP chat
compatible while requiring an explicit ``/investigate ...`` command by
default.  It also requires an explicit user and AgentSpace unless an operator
deliberately relaxes those checks.

The module name intentionally differs from ``aws_devops_agent`` so executing
this file cannot shadow the upstream package on ``sys.path``.

The optional upstream package is intentionally not an acp-bridge dependency.
Install it in an isolated environment and use that environment's Python as the
configured agent command.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Mapping
from typing import Any

INVESTIGATION_MODES = {"disabled", "explicit", "auto"}
TRUE_VALUES = {"1", "true", "yes", "on"}
FALSE_VALUES = {"0", "false", "no", "off"}


class ConfigurationError(ValueError):
    """Unsafe or incomplete launcher configuration."""


def _enabled(value: str | None, *, name: str, default: bool = False) -> bool:
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in TRUE_VALUES:
        return True
    if normalized in FALSE_VALUES:
        return False
    raise ConfigurationError(f"{name} must be a boolean value")


def load_settings(env: Mapping[str, str] | None = None) -> str:
    """Validate fail-closed settings and return the investigation mode."""
    values = os.environ if env is None else env
    mode = values.get("ACP_BRIDGE_AWS_INVESTIGATION_MODE", "explicit").strip().lower()
    if mode not in INVESTIGATION_MODES:
        allowed = ", ".join(sorted(INVESTIGATION_MODES))
        raise ConfigurationError(f"ACP_BRIDGE_AWS_INVESTIGATION_MODE must be one of: {allowed}")

    if not values.get("DEVOPS_AGENT_USER_ID", "").strip():
        raise ConfigurationError("DEVOPS_AGENT_USER_ID is required")

    require_space = _enabled(
        values.get("ACP_BRIDGE_AWS_REQUIRE_SPACE_ID"),
        name="ACP_BRIDGE_AWS_REQUIRE_SPACE_ID",
        default=True,
    )
    if require_space and not values.get("DEVOPS_AGENT_SPACE_ID", "").strip():
        raise ConfigurationError(
            "DEVOPS_AGENT_SPACE_ID is required by the safe launcher; set "
            "ACP_BRIDGE_AWS_REQUIRE_SPACE_ID=false only to allow read-only discovery"
        )

    auto_create = _enabled(
        values.get("DEVOPS_AGENT_AUTO_CREATE_SPACE"),
        name="DEVOPS_AGENT_AUTO_CREATE_SPACE",
    )
    allow_create = _enabled(
        values.get("ACP_BRIDGE_AWS_ALLOW_SPACE_CREATE"),
        name="ACP_BRIDGE_AWS_ALLOW_SPACE_CREATE",
    )
    if auto_create and not allow_create:
        raise ConfigurationError(
            "AgentSpace auto-creation is blocked; set both "
            "DEVOPS_AGENT_AUTO_CREATE_SPACE=true and "
            "ACP_BRIDGE_AWS_ALLOW_SPACE_CREATE=true to opt in"
        )

    return mode


def is_explicit_investigation(text: str) -> bool:
    """Return True only for a non-empty `/investigate ...` command."""
    stripped = text.lstrip()
    prefix = "/investigate"
    if not stripped.lower().startswith(prefix):
        return False
    remainder = stripped[len(prefix) :]
    return bool(remainder and remainder[0].isspace() and remainder.strip())


def build_server(investigation_mode: str) -> Any:
    """Build a restricted subclass of the optional upstream ACP server."""
    try:
        from aws_devops_agent.acp_server import ACPServer
    except ImportError as exc:
        raise ConfigurationError(
            "aws-devops-agent-acp is not installed in this Python environment"
        ) from exc

    class SafeAWSDevOpsACPServer(ACPServer):
        _bridge_investigation_mode = investigation_mode

        @classmethod
        def _looks_like_investigation(cls, text: str) -> bool:
            if cls._bridge_investigation_mode == "disabled":
                return False
            if cls._bridge_investigation_mode == "explicit":
                return is_explicit_investigation(text)
            return super()._looks_like_investigation(text)

    return SafeAWSDevOpsACPServer()


def main() -> int:
    try:
        investigation_mode = load_settings()
        server = build_server(investigation_mode)
    except ConfigurationError as exc:
        print(f"AWS DevOps Agent configuration error: {exc}", file=sys.stderr)
        return 2

    print(
        "AWS DevOps Agent safe launcher: "
        f"investigation_mode={investigation_mode}, auto_create_guard=enabled",
        file=sys.stderr,
    )
    server.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
