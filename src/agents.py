"""Agent handlers — ACP mode + PTY fallback with automatic fallback."""

import asyncio
import logging
import os
import re
import time
import uuid
from collections.abc import AsyncGenerator
from typing import Optional

from acp_sdk.models import Message, MessagePart
from acp_sdk.server import Context, RunYield, RunYieldResume

from .acp_client import AcpConnection, AcpError, AcpProcessPool, PoolExhaustedError
from .circuit_breaker import CircuitBreaker, CircuitBreakerConfig, CircuitBreakerOpenError, CircuitState
from .exceptions import AgentModelError, AgentRateLimitError, AgentTimeoutError
from .formatters import fmt
from .heartbeat import EnvCollector
from .sse import transform_notification
from .stats import StatsCollector

log = logging.getLogger("acp-bridge.agents")


# =============================================================================
# Fallback configuration
# =============================================================================

_DEFAULT_FALLBACK_CHAIN = {
    "kiro": ["claude", "opencode", "qwen"],
    "claude": ["opencode", "qwen", "kiro"],
    "opencode": ["kiro", "claude", "qwen"],
    "qwen": ["claude", "kiro", "opencode"],
    "hermes": ["claude", "kiro", "opencode"],
}

FALLBACK_CHAIN: dict[str, list[str]] = dict(_DEFAULT_FALLBACK_CHAIN)

_fallback_chain_path: str = ""


def load_fallback_chain(path: str) -> None:
    """Load FALLBACK_CHAIN from YAML. Falls back to defaults if file missing."""
    global _fallback_chain_path
    _fallback_chain_path = path
    try:
        import yaml
        with open(path) as f:
            data = yaml.safe_load(f)
        if isinstance(data, dict):
            FALLBACK_CHAIN.clear()
            FALLBACK_CHAIN.update(data)
            log.info("fallback_chain_loaded: path=%s agents=%s", path, list(data.keys()))
        else:
            log.warning("fallback_chain_invalid: path=%s, using defaults", path)
    except FileNotFoundError:
        log.info("fallback_chain_default: %s not found, using built-in defaults", path)
    except Exception as e:
        log.warning("fallback_chain_load_error: %s, using defaults: %s", path, e)


def save_fallback_chain() -> None:
    """Persist current FALLBACK_CHAIN to YAML."""
    if not _fallback_chain_path:
        return
    import yaml
    from pathlib import Path
    Path(_fallback_chain_path).parent.mkdir(parents=True, exist_ok=True)
    with open(_fallback_chain_path, "w") as f:
        yaml.dump(dict(FALLBACK_CHAIN), f, default_flow_style=False, allow_unicode=True)
    log.info("fallback_chain_saved: path=%s", _fallback_chain_path)


def get_next_fallback(failed_agent: str, tried_agents: list[str] | None = None) -> Optional[str]:
    """Get the next agent in the fallback chain (static order, no pool/stats needed)."""
    if tried_agents is None:
        tried_agents = []

    candidates = FALLBACK_CHAIN.get(failed_agent, [])
    for agent in candidates:
        if agent not in tried_agents:
            return agent
    return None


def get_best_fallback(
    failed_agent: str,
    tried_agents: list[str] | None = None,
    pool: AcpProcessPool | None = None,
    stats: StatsCollector | None = None,
) -> Optional[str]:
    """Pick the best fallback agent using live pool state + historical stats + circuit breaker state.

    Scoring (higher = better):
      1. has_idle  — agent has at least one idle connection (bool, prioritized)
      2. success_rate — recent success ratio (0.0–1.0)
      3. -avg_duration — prefer faster agents

    Agents with OPEN circuit breakers are filtered out from candidates.
    Falls back to static chain order when pool/stats are unavailable.
    """
    if tried_agents is None:
        tried_agents = []

    candidates = [a for a in FALLBACK_CHAIN.get(failed_agent, [])
                  if a not in tried_agents and is_agent_healthy(a)]
    if not candidates:
        # Fall back to all untried (including unhealthy) as last resort
        candidates = [a for a in FALLBACK_CHAIN.get(failed_agent, []) if a not in tried_agents]
    if not candidates:
        return None

    # No runtime data → static order
    if not pool and not stats:
        return candidates[0]

    # Filter out agents with OPEN circuit breakers (if available)
    open_breakers = []
    for agent in candidates:
        breaker = _circuit_breakers.get(agent)
        if breaker and breaker.state == CircuitState.OPEN:
            open_breakers.append(agent)

    if open_breakers:
        log.debug("fallback_filtered_open_breakers: agents=%s", open_breakers)
        candidates = [a for a in candidates if a not in open_breakers]
        if not candidates:
            return None

    def score(agent: str) -> float:
        # Circuit breaker weight: CLOSED=1.0, HALF_OPEN=0.5, OPEN already filtered above
        breaker = _circuit_breakers.get(agent)
        cb_weight = 0.5 if (breaker and breaker.state == CircuitState.HALF_OPEN) else 1.0
        # Pool: check for idle connections
        has_idle = False
        if pool:
            try:
                for (a, _), conn in list(pool._connections.items()):
                    if a == agent and conn.state == "idle":
                        has_idle = True
                        break
            except RuntimeError:
                pass

        # Dual-window: 1h base + 15min trend penalty
        s_1h = stats.get_agent_stats(agent, hours=1.0) if stats else {}
        s_15m = stats.get_agent_stats(agent, hours=0.25) if stats else {}
        rate_1h = s_1h.get("success_rate", 1.0)
        rate_15m = s_15m.get("success_rate", 1.0)
        avg_dur = s_1h.get("avg_duration", 30.0)

        base = 100 * rate_1h + 20 / (1 + avg_dur / 30)
        trend_penalty = max(0, (1 - rate_15m) - (1 - rate_1h)) * 50
        val = (base - trend_penalty) * (1.5 if has_idle else 1.0) * cb_weight
        log.debug("fallback_score: agent=%s score=%.1f (idle=%s rate_1h=%.2f rate_15m=%.2f dur=%.1f penalty=%.1f cb_weight=%.1f)",
                  agent, val, has_idle, rate_1h, rate_15m, avg_dur, trend_penalty, cb_weight)
        return val

    candidates.sort(key=score, reverse=True)
    best = candidates[0]
    log.info("fallback_decision: failed=%s tried=%s best=%s scores=%s",
              failed_agent, tried_agents, best,
              {a: score(a) for a in candidates[:3]})
    return best


# Module-level refs, set by main.py
_stats: StatsCollector | None = None
_env: EnvCollector | None = None
_pool: AcpProcessPool | None = None

# Per-agent health state (default True = optimistic on cold start)
_agent_healthy: dict[str, bool] = {}

# Per-agent circuit breakers
_circuit_breakers: dict[str, CircuitBreaker] = {}


def is_agent_healthy(agent: str) -> bool:
    return _agent_healthy.get(agent, True)


def get_circuit_breaker(agent: str) -> CircuitBreaker:
    """Get or create a circuit breaker for an agent."""
    if agent not in _circuit_breakers:
        _circuit_breakers[agent] = CircuitBreaker(
            name=agent,
            config=CircuitBreakerConfig(
                failure_threshold=5,
                failure_rate_threshold=0.5,
                window_size=10,
                open_timeout=30.0,
                half_open_max_calls=3,
                expected_exceptions=(AcpError, PoolExhaustedError, AgentTimeoutError,
                                     AgentModelError, asyncio.TimeoutError),
                excluded_exceptions=(AgentRateLimitError,),
                # NOTE: AgentRateLimitError intentionally excluded — rate-limit is a
                # client-side throttle, not an agent fault; do NOT trip the breaker.
            )
        )
        log.info("circuit_breaker_created: agent=%s", agent)
    return _circuit_breakers[agent]


async def ping_agent(pool: AcpProcessPool, agent: str, timeout: float = 5) -> bool:
    """Ping all idle connections for an agent. Returns True if at least one responds."""
    for (a, _), conn in list(pool._connections.items()):
        if a == agent and conn.state == "idle":
            if await conn.ping(timeout=timeout):
                return True
    return False


async def ping_loop(pool: AcpProcessPool, interval: float = 300):
    """Background task: periodically ping idle agents, update _agent_healthy."""
    while True:
        await asyncio.sleep(interval)
        pinged_agents: set[str] = set()
        for (agent, _), conn in list(pool._connections.items()):
            if agent in pinged_agents:
                continue
            if conn.state != "idle":
                continue
            pinged_agents.add(agent)
            ok = await ping_agent(pool, agent)
            _agent_healthy[agent] = ok
            if not ok:
                log.warning("ping_unhealthy: agent=%s marked unhealthy", agent)
            else:
                log.debug("ping_healthy: agent=%s", agent)
        # Reset agents no longer in pool to healthy (optimistic default)
        for agent in list(_agent_healthy):
            if not _agent_healthy[agent] and agent not in pinged_agents:
                _agent_healthy.pop(agent)
                log.info("ping_reset: agent=%s no longer in pool, cleared unhealthy mark", agent)

ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]|\x1b\[\?25[hl]")


def strip_ansi(text: str) -> str:
    return ANSI_RE.sub("", text)


async def _execute_agent_call(
    agent_name: str,
    prompt: str,
    pool: AcpProcessPool,
    profile: dict | None,
    session_id: str,
    cwd: str,
    enrich_prompt: bool = True,
) -> tuple[bool, list[RunYield]]:
    """Execute agent call and return (success, yielded_items). To be used with circuit breaker."""
    results: list[RunYield] = []
    _success = True

    conn: AcpConnection | None = None
    try:
        conn = await pool.get_or_create(agent_name, session_id, cwd=cwd, profile=profile)
    except (PoolExhaustedError, AcpError) as e:
        log.error("agent_init_failed: agent=%s error=%s", agent_name, e)
        if _stats:
            _stats.record(agent_name, session_id, False, 0)
        return False, results

    session_reset_yielded = False
    if conn.session_reset:
        results.append(MessagePart(
            content=fmt("agent", "session_expired",
                        "⚠️ 会话已过期，已自动创建新会话（之前的对话上下文已丢失）") + "\n",
            content_type="text/plain"))
        session_reset_yielded = True
        conn.session_reset = False

    _t0 = time.time()
    _tools_used: list[str] = []
    last_yield_time = asyncio.get_event_loop().time()
    heartbeat_interval = 15

    enriched = prompt
    if enrich_prompt and _env:
        prefix = _env.get_prefix(agent_name)
        if prefix:
            enriched = prefix + prompt

    try:
        async for notification in conn.session_prompt(enriched):
            if "_prompt_result" in notification:
                log.info("acp_done: agent=%s session=%s stop=%s",
                         agent_name, session_id,
                         notification["_prompt_result"].get("result", {}).get("stopReason", "?"))
                if "error" in notification["_prompt_result"]:
                    _success = False
                continue

            event = transform_notification(notification)
            if event is None:
                now = asyncio.get_event_loop().time()
                if now - last_yield_time > heartbeat_interval:
                    results.append(MessagePart(content="", content_type="text/plain", name="heartbeat"))
                    last_yield_time = now
                continue

            last_yield_time = asyncio.get_event_loop().time()

            if event["type"] == "message.part":
                results.append(MessagePart(content=event["content"], content_type="text/plain"))
            elif event["type"] == "message.thinking":
                results.append(MessagePart(content=event["content"], content_type="text/plain", name="thought"))
            elif event["type"] in ("tool.start", "tool.done"):
                detail = event.get('status', '')
                if event.get('status') == 'error' and event.get('output'):
                    detail = event['output']
                if event["type"] == "tool.done" and event.get("title"):
                    _tools_used.append(event["title"])
                results.append(MessagePart(
                    content=f"[{event['type']}] {event.get('title', '')} ({detail})\n",
                    content_type="text/plain"))
            elif event["type"] == "status":
                results.append(MessagePart(content=f"[status] {event['text']}\n", content_type="text/plain"))

        if _stats:
            _stats.record(agent_name, session_id, _success, time.time() - _t0, _tools_used)

    except Exception as e:
        log.error("agent_crashed: agent=%s session=%s error=%s", agent_name, session_id, e)
        pool.remove(agent_name, session_id)
        if _stats:
            _stats.record(agent_name, session_id, False, time.time() - _t0, _tools_used)
        # Classify for smart retry
        msg = str(e).lower()
        if isinstance(e, (AgentTimeoutError, AgentRateLimitError, AgentModelError)):
            raise
        if "timeout" in msg or "idle" in msg:
            raise AgentTimeoutError(str(e)) from e
        if "rate limit" in msg or "429" in msg:
            raise AgentRateLimitError(str(e)) from e
        if isinstance(e, AcpError):
            raise AgentModelError(str(e)) from e
        raise

    return _success, results


async def _call_acp_agent_internal(
    agent_name: str,
    prompt: str,
    pool: AcpProcessPool,
    profile: dict | None,
    session_id: str,
    cwd: str,
    enrich_prompt: bool = True,
) -> AsyncGenerator[RunYield, RunYieldResume]:
    """
    Internal async generator function to call an ACP agent.

    Yields: RunYield (MessagePart) objects
    Raises: AcpError, PoolExhaustedError on failure
    """
    log.info("acp_start: agent=%s session=%s len=%d cwd=%s",
             agent_name, session_id, len(prompt), cwd or "(default)")

    # Wrap the execution in circuit breaker
    breaker = get_circuit_breaker(agent_name)
    
    try:
        success, results = await breaker.call(
            _execute_agent_call, agent_name, prompt, pool, profile, session_id, cwd, enrich_prompt
        )
        for result in results:
            yield result
        if not success:
            # Mark as failure manually since we collected all yields but got error in _prompt_result
            breaker.record_failure()
    except CircuitBreakerOpenError as e:
        log.warning("circuit_breaker_open: agent=%s error=%s", agent_name, e)
        raise AgentModelError(f"Circuit breaker open for {agent_name}") from e


def make_acp_agent_handler(agent_name: str, pool: AcpProcessPool, profile: dict | None = None):
    """ACP protocol handler with automatic fallback on errors."""

    async def handler(
        input: list[Message], context: Context
    ) -> AsyncGenerator[RunYield, RunYieldResume]:
        prompt = "".join(part.content for msg in input for part in msg.parts if part.content)
        # Extract metadata: cwd, channel_id, explicit session_id
        cwd = ""
        channel_id = ""
        explicit_session = ""
        for msg in input:
            for part in msg.parts:
                if part.metadata:
                    if not cwd and part.metadata.get("cwd"):
                        cwd = part.metadata["cwd"]
                    if not channel_id and part.metadata.get("channel_id"):
                        channel_id = part.metadata["channel_id"]
                    if not explicit_session and part.metadata.get("session_id"):
                        explicit_session = part.metadata["session_id"]

        # Session reuse strategy:
        # 1. channel_id → per (agent, channel) — IM scenarios
        # 2. explicit session_id in metadata → caller override
        # 3. fallback → one process per agent
        if channel_id:
            session_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"{agent_name}:{channel_id}"))
        elif explicit_session:
            session_id = explicit_session
        else:
            session_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, agent_name))

        MAX_FALLBACK_ATTEMPTS = 3
        tried_agents: list[str] = []
        original_agent = agent_name
        original_session_id = session_id
        current_agent = agent_name

        for attempt in range(MAX_FALLBACK_ATTEMPTS):
            tried_agents.append(current_agent)
            try:
                # Call the internal agent handler
                async for part in _call_acp_agent_internal(
                    agent_name=current_agent,
                    prompt=prompt,
                    pool=pool,
                    profile=profile,
                    session_id=session_id,
                    cwd=cwd,
                    enrich_prompt=attempt == 0,  # Only enrich on first attempt
                ):
                    yield part

                # If we fell back to a different agent, yield a special message
                if current_agent != original_agent:
                    import json as _json
                    context_lost = (session_id != original_session_id)
                    fallback_meta = {
                        "type": "fallback",
                        "original_agent": original_agent,
                        "fallback_agent": current_agent,
                        "context_preserved": not context_lost,
                        "reason": "agent_error",
                        "tried_agents": tried_agents,
                    }
                    content = (
                        f"⚠️ Fallback: {original_agent} → {current_agent}\n"
                        f"{'⚠️ Context lost' if context_lost else '✓ Context preserved'}\n"
                        f"<!-- {_json.dumps(fallback_meta)} -->"
                    )
                    yield Message(parts=[MessagePart(
                        content=content,
                        content_type="text/plain",
                        name="fallback_info",
                    )])
                    log.info("fallback_success: original=%s fallback=%s", original_agent, current_agent)
                    if _stats:
                        _stats.record_fallback(original_agent, current_agent, tried_agents, True)
                    return

                # No fallback needed, we're done
                return

            except AgentTimeoutError:
                log.warning("agent_timeout: agent=%s attempt=%d, retrying async",
                           current_agent, attempt + 1)
                # Record failure before retry
                timeout_breaker = get_circuit_breaker(current_agent)
                timeout_breaker.record_failure()
                try:
                    async for part in _call_acp_agent_internal(
                        agent_name=current_agent, prompt=prompt, pool=pool,
                        profile=profile, session_id=session_id, cwd=cwd,
                        enrich_prompt=False,
                    ):
                        yield part
                    return
                except Exception:
                    log.warning("agent_timeout_async_retry_failed: agent=%s", current_agent)

            except AgentRateLimitError as e:
                log.warning("agent_rate_limited: agent=%s retry_after=%d",
                           current_agent, e.retry_after)
                # Record failure before retry
                ratelimit_breaker = get_circuit_breaker(current_agent)
                # Do NOT record_failure for rate-limit — it's client-side throttle, not agent fault
                log.debug("agent_rate_limit_no_breaker_trip: agent=%s", current_agent)
                await asyncio.sleep(min(e.retry_after, 30))
                try:
                    async for part in _call_acp_agent_internal(
                        agent_name=current_agent, prompt=prompt, pool=pool,
                        profile=profile, session_id=session_id, cwd=cwd,
                        enrich_prompt=False,
                    ):
                        yield part
                    return
                except Exception:
                    log.warning("agent_rate_limit_retry_failed: agent=%s", current_agent)

            except (AcpError, PoolExhaustedError, AgentModelError) as e:
                log.warning("agent_failed: agent=%s attempt=%d error=%s",
                           current_agent, attempt + 1, e)

                # Record failure to circuit breaker for the failed agent
                failing_breaker = get_circuit_breaker(current_agent)
                failing_breaker.record_failure()

                if attempt >= MAX_FALLBACK_ATTEMPTS - 1:
                    # No more fallback attempts available
                    log.error("fallback_exhausted: agent=%s tried=%s",
                             original_agent, tried_agents)
                    if _stats:
                        _stats.record(original_agent, session_id, False, 0)
                        _stats.record_fallback(original_agent, current_agent, tried_agents, False)
                    yield Message(parts=[MessagePart(
                        content=fmt("agent", "fallback_exhausted",
                                   "[error] all fallback agents unavailable (tried: {})",
                                   agent=original_agent, detail=",".join(tried_agents)),
                        content_type="text/plain")])
                    return

                # Try next fallback agent
                next_agent = get_best_fallback(current_agent, tried_agents, pool, _stats)
                if next_agent is None:
                    log.error("no_fallback_available: agent=%s tried=%s",
                             original_agent, tried_agents)
                    if _stats:
                        _stats.record(original_agent, session_id, False, 0)
                        _stats.record_fallback(original_agent, current_agent, tried_agents, False)
                    yield Message(parts=[MessagePart(
                        content=fmt("agent", "no_fallback",
                                   "[error] no fallback available for {} (tried: {})",
                                   agent=original_agent, detail=",".join(tried_agents)),
                        content_type="text/plain")])
                    return

                log.info("fallback: agent=%s -> %s (attempt %d/%d)",
                        current_agent, next_agent, attempt + 1, MAX_FALLBACK_ATTEMPTS)
                # Reset session for the new agent
                session_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"{current_agent}:{session_id[:8]}"))

    return handler


def make_pty_agent_handler(agent_cfg: dict, verbose: bool = False):
    """Legacy PTY handler — subprocess stdout line-by-line."""
    command = agent_cfg["command"]
    args = agent_cfg.get("args", [])
    idle_timeout = agent_cfg.get("idle_timeout", 300)
    max_duration = agent_cfg.get("max_duration", 600)

    async def handler(
        input: list[Message], context: Context
    ) -> AsyncGenerator[RunYield, RunYieldResume]:
        prompt = "".join(part.content for msg in input for part in msg.parts if part.content)
        session_id = str(context.session.id) if context.session else "default"

        log.info("pty_start: cmd=%s session=%s", command, session_id)
        _t0 = time.time()

        env = os.environ.copy()
        env.update({"TERM": "dumb", "NO_COLOR": "1", "LANG": "en_US.UTF-8"})
        env.update(agent_cfg.get("env", {}))

        cmd = [command] + list(args) + [prompt]
        pty_cwd = agent_cfg.get("working_dir", "/tmp")
        os.makedirs(pty_cwd, exist_ok=True)

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            stdin=asyncio.subprocess.DEVNULL,
            cwd=pty_cwd,
            env=env,
        )

        try:
            while True:
                if time.time() - _t0 > max_duration:
                    log.warning("pty_max_duration: cmd=%s session=%s dur=%ds", command, session_id, max_duration)
                    proc.kill()
                    await proc.wait()
                    if _stats:
                        _stats.record(command, session_id, False, time.time() - _t0)
                    yield MessagePart(
                        content=fmt("agent", "agent_timeout", "[error] agent exceeded max_duration ({timeout}s)",
                                    agent=command, timeout=max_duration) + "\n",
                        content_type="text/plain")
                    return
                try:
                    line = await asyncio.wait_for(proc.stdout.readline(), timeout=idle_timeout)
                except asyncio.TimeoutError:
                    log.warning("pty_timeout: cmd=%s session=%s idle=%ds", command, session_id, idle_timeout)
                    proc.kill()
                    await proc.wait()
                    if _stats:
                        _stats.record(command, session_id, False, time.time() - _t0)
                    yield MessagePart(
                        content=fmt("agent", "agent_timeout", "[error] agent timeout (idle {timeout}s)",
                                    agent=command, timeout=idle_timeout) + "\n",
                        content_type="text/plain")
                    return
                except (asyncio.LimitOverrunError, ValueError):
                    log.warning("pty_line_too_long: cmd=%s session=%s", command, session_id)
                    continue
                if not line:
                    break
                text = strip_ansi(line.decode()).rstrip("\n")
                if text:
                    yield MessagePart(content=text + "\n", content_type="text/plain")

            await proc.wait()
        except Exception:
            proc.kill()
            await proc.wait()
            if _stats:
                _stats.record(command, session_id, False, time.time() - _t0)
            raise
        if _stats:
            _stats.record(command, session_id, proc.returncode == 0, time.time() - _t0)
        log.info("pty_done: cmd=%s session=%s exit=%s", command, session_id, proc.returncode)

    return handler
