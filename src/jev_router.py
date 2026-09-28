"""Jev router — let TypeSafe AI's System One model (Jev) pick the agent for a task.

A virtual agent (default name ``auto``) is registered next to the real ones. When it
receives a prompt it asks Jev one *Choice* question — "which of these agents should
run this task?" — with one option per eligible local agent plus ``other``. The answer
comes back as a calibrated probability distribution; if the top option clears
``confidence_threshold`` the request is handed to that agent's handler unchanged,
otherwise (low confidence, ``other``, or any Jev failure) it goes to ``default_agent``.

Design constraints:
- Fail-safe: a Jev outage must never fail the caller's task. Every error path
  degrades to ``default_agent`` and reports the reason in the ``route_info`` part.
- Zero footprint when disabled: nothing in this module is imported by the request
  path unless ``router.enabled`` is true in config.
- No secrets in logs/responses: the API key lives only inside the httpx client's
  default headers. ``RouteDecision`` and ``stats`` never carry it, and the client
  is built with ``trust_env=False`` so proxy env vars cannot redirect the call.
- Only the official HTTP API is used (``POST {base_url}/v1/systemone``); the
  request/response shapes follow https://docs.typesafe.ai/api.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import AsyncGenerator, Callable, Mapping
from dataclasses import asdict, dataclass, field
from urllib.parse import urlsplit

import httpx
from acp_sdk.models import Message, MessagePart
from acp_sdk.server import Context, RunYield, RunYieldResume

log = logging.getLogger("acp-bridge.router")

DEFAULT_BASE_URL = "https://api.typesafe.ai"
DEFAULT_MODEL = "jev-latest"
DEFAULT_TIMEOUT = 10.0
DEFAULT_CONFIDENCE_THRESHOLD = 0.5
DEFAULT_MAX_STATE_CHARS = 6000
OTHER = "other"

# Trust levels that are never routed to implicitly (must be listed in `candidates`).
_IMPLICIT_EXCLUDED_TRUST = {"unrestricted", 2, "2"}


@dataclass
class RouteDecision:
    """Outcome of one routing question. Safe to serialise to callers (no secrets)."""

    agent: str  # agent that will actually run the task
    reason: str  # "jev" | "low_confidence" | "other" | "invalid_choice" | "jev_error:<kind>"
    fallback: bool  # True when `agent` is default_agent rather than Jev's pick
    jev_choice: str = ""  # raw option Jev picked ("" if Jev was not consulted/failed)
    confidence: float = 0.0
    probabilities: dict[str, float] = field(default_factory=dict)  # top-N, sorted desc
    model: str = ""  # resolved model id reported by the API (e.g. jev-1.13.0)
    latency_ms: int = 0
    input_tokens: int = 0
    error: str = ""  # sanitised error summary when reason starts with jev_error

    def to_dict(self) -> dict:
        return asdict(self)


class JevRouter:
    """Builds the Choice criteria from agent config and asks Jev which agent fits."""

    def __init__(
        self,
        api_key: str,
        agents_cfg: Mapping[str, object],
        *,
        agent_name: str = "auto",
        default_agent: str,
        model: str = DEFAULT_MODEL,
        timeout: float = DEFAULT_TIMEOUT,
        confidence_threshold: float = DEFAULT_CONFIDENCE_THRESHOLD,
        candidates: list[str] | None = None,
        exclude: list[str] | None = None,
        max_state_chars: int = DEFAULT_MAX_STATE_CHARS,
        base_url: str = DEFAULT_BASE_URL,
        top_n: int = 3,
        http_client: httpx.AsyncClient | None = None,
    ):
        key = (api_key or "").strip()
        if not key:
            raise ValueError("router: api_key is empty (set TYPESAFE_API_KEY in .env)")
        if not (0.0 <= confidence_threshold <= 1.0):
            raise ValueError("router: confidence_threshold must be within [0, 1]")
        base = (base_url or DEFAULT_BASE_URL).rstrip("/")
        if urlsplit(base).scheme != "https":
            # The API key travels in a header; refuse to send it in clear text.
            raise ValueError("router: base_url must use https")

        self.agent_name = agent_name
        self.default_agent = default_agent
        self.model = model
        self.timeout = float(timeout)
        self.confidence_threshold = float(confidence_threshold)
        self.max_state_chars = int(max_state_chars)
        self.top_n = int(top_n)
        self.base_url = base
        self.criteria = self.build_criteria(
            agents_cfg, agent_name=agent_name, candidates=candidates, exclude=exclude
        )
        self.candidate_agents = [k for k in self.criteria if k != OTHER]
        if default_agent not in agents_cfg:
            raise ValueError(f"router: default_agent {default_agent!r} is not a configured agent")
        if not self.candidate_agents:
            raise ValueError("router: no eligible candidate agents after filtering")

        self.stats: dict = {
            "decisions": 0,
            "routed": 0,
            "fallback": 0,
            "jev_errors": 0,
            "by_agent": {},
            "by_reason": {},
            "input_tokens": 0,
        }
        self._owns_client = http_client is None
        self._client = http_client or httpx.AsyncClient(
            base_url=self.base_url,
            timeout=self.timeout,
            trust_env=False,
            headers={
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
                "User-Agent": "acp-bridge-router",
            },
        )

    def __repr__(self) -> str:  # never include the key
        return (
            f"JevRouter(agent={self.agent_name!r}, model={self.model!r}, "
            f"threshold={self.confidence_threshold}, candidates={self.candidate_agents})"
        )

    # ------------------------------------------------------------------ criteria
    @staticmethod
    def build_criteria(
        agents_cfg: Mapping[str, object],
        *,
        agent_name: str = "auto",
        candidates: list[str] | None = None,
        exclude: list[str] | None = None,
    ) -> dict[str, object]:
        """One Choice option per eligible agent, described from its config, plus `other`.

        Eligible = enabled, local (not pool=lambda), not the router itself, not in
        `exclude`, and (if `candidates` given) in that allowlist. Agents with
        trust=unrestricted are skipped unless explicitly named in `candidates`.
        """
        allow = set(candidates or [])
        deny = set(exclude or [])
        crit: dict[str, object] = {}
        for name, cfg in agents_cfg.items():
            if not isinstance(cfg, dict) or name == agent_name or name in deny:
                continue
            if not cfg.get("enabled", True) or cfg.get("pool", "local") == "lambda":
                continue
            if allow and name not in allow:
                continue
            if not allow and cfg.get("trust") in _IMPLICIT_EXCLUDED_TRUST:
                continue
            caps = cfg.get("capabilities") or {}
            md = cfg.get("metadata") or {}
            domains = sorted({*(caps.get("domains") or []), *(md.get("domains") or [])})
            tags = sorted({*(caps.get("tags") or []), *(md.get("tags") or [])})
            desc: dict[str, object] = {"what": cfg.get("description") or name}
            if domains:
                desc["domains"] = domains
            if tags:
                desc["tags"] = tags
            crit[name] = desc
        crit[OTHER] = "None of the listed agents is clearly suitable for this task"
        return crit

    # ------------------------------------------------------------------ decide
    def _payload(self, prompt: str) -> dict:
        state = prompt if len(prompt) <= self.max_state_chars else prompt[: self.max_state_chars]
        return {
            "state": {"task": state},
            "model": self.model,
            "questions": {
                "agent": {
                    "type": "choice",
                    "instructions": (
                        "Which agent should execute this task? Pick the single best fit "
                        "based on the task's domain and each agent's strengths. Choose "
                        f"'{OTHER}' if none is clearly suitable."
                    ),
                    "criteria": self.criteria,
                }
            },
        }

    async def decide(self, prompt: str) -> RouteDecision:
        """Ask Jev; never raises. Any failure degrades to default_agent."""
        t0 = time.perf_counter()
        try:
            resp = await self._client.post("/v1/systemone", json=self._payload(prompt))
        except httpx.TimeoutException:
            return self._record(self._fallback("jev_error:timeout", f"timeout>{self.timeout}s", t0))
        except httpx.HTTPError as e:
            return self._record(self._fallback("jev_error:connection", type(e).__name__, t0))

        if resp.status_code != 200:
            kind = "rate_limit" if resp.status_code == 429 else f"http_{resp.status_code}"
            return self._record(self._fallback(f"jev_error:{kind}", f"HTTP {resp.status_code}", t0))

        try:
            data = resp.json()
            answer = data["answers"]["agent"]
            choice = str(answer["choice"])
            probs_raw = answer.get("probabilities") or {}
            probabilities = {str(k): float(v) for k, v in probs_raw.items()}
            confidence = float(answer.get("confidence", 0.0))
            model = str(data.get("model", ""))
            usage = data.get("usage") or {}
            input_tokens = int(usage.get("input_tokens", 0) or 0)
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as e:
            return self._record(self._fallback("jev_error:bad_response", type(e).__name__, t0))

        top = dict(sorted(probabilities.items(), key=lambda kv: -kv[1])[: self.top_n])
        base = dict(
            jev_choice=choice,
            confidence=confidence,
            probabilities=top,
            model=model,
            latency_ms=int((time.perf_counter() - t0) * 1000),
            input_tokens=input_tokens,
        )
        if choice == OTHER:
            d = RouteDecision(self.default_agent, "other", True, **base)
        elif choice not in self.candidate_agents:
            # Defensive: the API should only return keys we sent, but never trust it.
            d = RouteDecision(self.default_agent, "invalid_choice", True, **base)
        elif confidence < self.confidence_threshold:
            d = RouteDecision(self.default_agent, "low_confidence", True, **base)
        else:
            d = RouteDecision(choice, "jev", False, **base)
        return self._record(d)

    def _fallback(self, reason: str, error: str, t0: float) -> RouteDecision:
        log.warning("router: jev unavailable (%s: %s) -> %s", reason, error, self.default_agent)
        return RouteDecision(
            self.default_agent,
            reason,
            True,
            latency_ms=int((time.perf_counter() - t0) * 1000),
            error=error,
        )

    def _record(self, d: RouteDecision) -> RouteDecision:
        s = self.stats
        s["decisions"] += 1
        s["fallback" if d.fallback else "routed"] += 1
        if d.reason.startswith("jev_error"):
            s["jev_errors"] += 1
        s["by_agent"][d.agent] = s["by_agent"].get(d.agent, 0) + 1
        s["by_reason"][d.reason] = s["by_reason"].get(d.reason, 0) + 1
        s["input_tokens"] += d.input_tokens
        log.info(
            "route: %s -> %s reason=%s choice=%s conf=%.2f %dms",
            self.agent_name,
            d.agent,
            d.reason,
            d.jev_choice or "-",
            d.confidence,
            d.latency_ms,
        )
        return d

    async def resolve(self, prompt: str, available: Callable[[str], bool]) -> RouteDecision:
        """decide() + guard: if Jev's pick is not actually runnable right now, use default.

        `available(name)` tells whether the caller can dispatch to that agent (live SDK
        registry for /runs, pool/pty/registry for /jobs). Never raises.
        """
        decision = await self.decide(prompt)
        if decision.agent != self.default_agent and not available(decision.agent):
            log.warning("router: chosen agent %s not available, using default", decision.agent)
            decision = RouteDecision(
                self.default_agent,
                "invalid_choice",
                True,
                jev_choice=decision.jev_choice,
                confidence=decision.confidence,
                probabilities=decision.probabilities,
                model=decision.model,
                latency_ms=decision.latency_ms,
                input_tokens=decision.input_tokens,
            )
        return decision

    def route_info_line(self, decision: RouteDecision) -> str:
        """Human line + JSON comment, same shape as agents.py's fallback_info. No secrets."""
        meta = decision.to_dict()
        meta["router"] = self.agent_name
        return (
            f"🧭 Route: {self.agent_name} → {decision.agent} (jev: {decision.jev_choice or '-'}, "
            f"confidence {decision.confidence:.2f}, {decision.reason})\n"
            f"<!-- {json.dumps(meta)} -->"
        )

    def status(self) -> dict:
        """Public snapshot for GET /route/status. Contains no secrets."""
        return {
            "enabled": True,
            "agent_name": self.agent_name,
            "model": self.model,
            "base_url": self.base_url,
            "timeout": self.timeout,
            "confidence_threshold": self.confidence_threshold,
            "max_state_chars": self.max_state_chars,
            "default_agent": self.default_agent,
            "candidates": list(self.candidate_agents),
            "stats": json.loads(json.dumps(self.stats)),
        }

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()


# ---------------------------------------------------------------------- handler
def make_router_agent_handler(router: JevRouter, live_agents: Callable[[], Mapping]):
    """ACP handler for the virtual agent: decide, announce, then delegate unchanged.

    `live_agents` returns the SDK's live agent registry (app.state.acp_agents) at call
    time, so agents registered later (harness, mesh, lambda) are still reachable —
    though only config-declared local agents are ever *chosen* (see build_criteria).
    """

    async def handler(
        input: list[Message], context: Context
    ) -> AsyncGenerator[RunYield, RunYieldResume]:
        prompt = "".join(part.content for msg in input for part in msg.parts if part.content)
        registry = live_agents() or {}
        decision = await router.resolve(prompt, lambda name: name in registry)
        target_name = decision.agent
        target = registry.get(target_name)
        line = router.route_info_line(decision)
        yield Message(
            parts=[MessagePart(content=line, content_type="text/plain", name="route_info")]
        )

        if target is None:
            yield MessagePart(
                content=f"[error] router: default agent {target_name!r} is not registered",
                content_type="text/plain",
            )
            return

        async for item in target.run(input, context):
            yield item

    return handler
