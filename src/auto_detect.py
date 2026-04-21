"""Auto-detect agent CLIs in PATH — zero-config fallback."""

import logging
import os
import secrets
import shutil

log = logging.getLogger("acp-bridge.auto_detect")

KNOWN_AGENTS = {
    "kiro": {
        "command": "kiro-cli",
        "mode": "acp",
        "acp_args": ["acp", "--trust-all-tools"],
        "description": "Kiro CLI agent",
    },
    "claude": {
        "command": "claude-agent-acp",
        "mode": "acp",
        "acp_args": [],
        "description": "Claude Code agent (via ACP adapter)",
    },
    "codex": {
        "command": "codex",
        "mode": "pty",
        "args": ["exec", "--full-auto", "--skip-git-repo-check"],
        "description": "OpenAI Codex CLI agent",
    },
    "qwen": {
        "command": "qwen",
        "mode": "acp",
        "acp_args": ["--acp"],
        "description": "Qwen Code agent",
    },
    "opencode": {
        "command": "opencode",
        "mode": "acp",
        "acp_args": ["acp"],
        "description": "OpenCode agent (open source, multi-provider)",
    },
    "hermes": {
        "command": "hermes",
        "mode": "acp",
        "acp_args": ["acp"],
        "description": "Hermes Agent (open source, multi-provider)",
    },
    "openclaw": {
        "command": "openclaw",
        "mode": "acp",
        "acp_args": ["acp"],
        "description": "OpenClaw Gateway (ACP bridge)",
    },
    "trae": {
        "command": "trae-cli",
        "mode": "pty",
        "args": ["run", "--working-dir", "/tmp/trae"],
        "working_dir": "/tmp/trae",
        "description": "Trae Agent (ByteDance, PTY mode via LiteLLM)",
    },
    "harness": {
        "command": "harness-factory",
        "mode": "acp",
        "acp_args": [],
        "description": "Harness Factory lite agent (profile-driven)",
    },
}


def detect_agents() -> dict:
    """Scan PATH for known agent CLIs, return agents config dict."""
    agents = {}
    for name, cfg in KNOWN_AGENTS.items():
        path = shutil.which(cfg["command"])
        if path:
            agents[name] = {**cfg, "enabled": True, "working_dir": "/tmp"}
            log.info("auto_detect: found %s at %s", name, path)
        else:
            log.debug("auto_detect: %s (%s) not found", name, cfg["command"])
    return agents


def build_config() -> dict:
    """Build a complete config dict from auto-detected agents."""
    agents = detect_agents()
    token = os.environ.get("ACP_BRIDGE_TOKEN", secrets.token_urlsafe(24))
    return {
        "server": {
            "host": "127.0.0.1",
            "port": 18010,
            "session_ttl_hours": 24,
            "shutdown_timeout": 30,
        },
        "pool": {"max_processes": 8, "max_per_agent": 4},
        "security": {"auth_token": token, "allowed_ips": ["127.0.0.1"]},
        "agents": agents,
    }
