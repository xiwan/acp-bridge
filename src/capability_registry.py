"""Agent capability discovery — load, search, and match agent capabilities."""

import logging
import re
import yaml
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

log = logging.getLogger("acp-bridge.capability_registry")


@dataclass
class AgentCapabilities:
    agent_name: str
    domains: List[str]
    tools: List[str]
    languages: Dict[str, Any]
    max_complexity: Optional[int] = None
    tags: List[str] = field(default_factory=list)
    version: Optional[str] = None
    timeout: Optional[int] = None
    cost_per_million_tokens: Optional[float] = None


class CapabilityRegistry:
    def __init__(self) -> None:
        self._capabilities: Dict[str, AgentCapabilities] = {}
        self._config_path: str = ""

    def load(self, config_path: str) -> None:
        """Load all agent capabilities from config.yaml."""
        self._config_path = config_path
        with open(config_path) as f:
            config = yaml.safe_load(f.read())
        new_caps: Dict[str, AgentCapabilities] = {}
        for name, cfg in (config or {}).get("agents", {}).items():
            if not cfg.get("enabled", True):
                continue
            cap_cfg = cfg.get("capabilities")
            if not cap_cfg:
                continue
            cap = AgentCapabilities(
                agent_name=name,
                domains=cap_cfg.get("domains", []),
                tools=cap_cfg.get("tools", []),
                languages=_normalize_languages(cap_cfg.get("languages", {})),
                max_complexity=cap_cfg.get("max_complexity"),
                tags=cap_cfg.get("tags", []),
                version=cap_cfg.get("version"),
                timeout=cap_cfg.get("timeout"),
                cost_per_million_tokens=cap_cfg.get("cost_per_million_tokens"),
            )
            new_caps[name] = cap
        self._capabilities = new_caps  # atomic replace
        log.info("capabilities_loaded: agents=%s", list(new_caps.keys()))

    def reload(self) -> bool:
        """Hot-reload capabilities; returns True if agent set changed."""
        old_keys = set(self._capabilities)
        self.load(self._config_path)
        return set(self._capabilities) != old_keys

    def get_agent(self, agent: str) -> Optional[AgentCapabilities]:
        return self._capabilities.get(agent)

    def list_all(self) -> List[AgentCapabilities]:
        return list(self._capabilities.values())

    def search(self, requirements: dict) -> List[Tuple[str, float]]:
        """Match capabilities and return (agent_name, score) sorted desc."""
        candidates = []
        for name, cap in list(self._capabilities.items()):  # snapshot to avoid RuntimeError on concurrent reload
            score = _score_agent(cap, requirements)
            if score > 0:
                candidates.append((name, score))
        candidates.sort(key=lambda x: x[1], reverse=True)
        return candidates

    def get_best(self, requirements: dict) -> Optional[str]:
        """Return the best-matching agent name, or None."""
        results = self.search(requirements)
        return results[0][0] if results else None


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _normalize_languages(raw: Any) -> Dict[str, Any]:
    """Normalize languages list/dict from YAML into {lang: value}."""
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, list):
        out: Dict[str, Any] = {}
        for item in raw:
            if isinstance(item, dict):
                out.update(item)
            elif isinstance(item, str):
                out[item] = True
        return out
    return {}


def _score_agent(cap: AgentCapabilities, req: dict) -> float:
    score = 100.0

    # domains — hard requirement
    req_domains = req.get("domains", [])
    if req_domains:
        matches = [d for d in req_domains if d in cap.domains]
        if not matches:
            return 0.0
        score += len(matches) * 10

    # tools — hard requirement (must have ALL)
    req_tools = req.get("tools", [])
    if req_tools:
        if not all(t in cap.tools for t in req_tools):
            return 0.0
        score += len(req_tools) * 5

    # languages — hard requirement
    req_langs = req.get("languages", {})
    for lang, req_ver in req_langs.items():
        if lang not in cap.languages:
            return 0.0
        if not _version_match(cap.languages[lang], req_ver):
            return 0.0
    score += len(req_langs) * 3

    # max_complexity
    req_complexity = req.get("max_complexity")
    if req_complexity is not None and cap.max_complexity is not None:
        if cap.max_complexity < req_complexity:
            score -= 20
        else:
            score += 5

    # cost constraint — hard exclusion (over-budget agent is never acceptable)
    max_cost = req.get("max_cost")
    if max_cost is not None and cap.cost_per_million_tokens is not None:
        if cap.cost_per_million_tokens > max_cost:
            return 0.0

    # tag filtering
    for t in req.get("exclude_tags", []):
        if t in cap.tags:
            score -= 30
    for t in req.get("prefer_tags", []):
        if t in cap.tags:
            score += 5

    return max(0.0, score)


def _version_match(cap_ver: Any, req_ver: Any) -> bool:
    """Check if agent's language version satisfies the requirement."""
    if cap_ver is True or req_ver is True:
        return True
    if isinstance(req_ver, list):
        # require at least one version match
        return any(_single_version_match(cap_ver, v) for v in req_ver)
    return _single_version_match(cap_ver, req_ver)


def _single_version_match(cap_ver: Any, req_ver: Any) -> bool:
    if cap_ver is True:
        return True
    if isinstance(req_ver, str) and req_ver.endswith("+"):
        # "3.10+" means >= 3.10
        req_tuple = _parse_ver(req_ver.rstrip("+"))
        if req_tuple is None:
            return False
        if isinstance(cap_ver, list):
            return any(_parsed_ge(v, req_tuple) for v in cap_ver)
        return _parsed_ge(cap_ver, req_tuple)
    # exact membership
    if isinstance(cap_ver, list):
        return str(req_ver) in [str(v) for v in cap_ver]
    return str(cap_ver) == str(req_ver)


def _parse_ver(v: str) -> Optional[tuple]:
    m = re.match(r'^([\d.]+)', str(v).rstrip("+"))
    if not m:
        return None
    return tuple(int(x) for x in m.group(1).split("."))


def _parsed_ge(cap_v: Any, req_tuple: tuple) -> bool:
    """Return True if cap_v >= req_tuple."""
    ct = _parse_ver(str(cap_v).rstrip("+"))
    if ct is None:
        return False
    return ct >= req_tuple
