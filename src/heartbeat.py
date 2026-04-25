"""Environment snapshot collector for agent awareness."""

import json
import logging
import time
from collections import deque
from pathlib import Path

from .acp_client import AcpProcessPool
from .formatters import get_template

log = logging.getLogger("acp-bridge.heartbeat")

_DEFAULT_HEARTBEAT_PROMPT = (
    "[HEARTBEAT] You are '{agent_name}'.\n"
    "Online agents:\n{agents_json}\n\n"
    "To talk to another agent:\n"
    '  {client} -a <agent_name> "<message>"\n\n'
    "Shared workspace: {shared_workdir}\n"
    "  All agents can read/write here for collaboration.\n\n"
    "If nothing to do, reply: [SILENT]"
)

_HEARTBEAT_PROMPT_ZH = (
    "[HEARTBEAT] 你是 '{agent_name}'。\n"
    "在线 agent：\n{agents_json}\n\n"
    "与其他 agent 对话：\n"
    '  {client} -a <agent_name> "<消息>"\n\n'
    "共享工作区：{shared_workdir}\n"
    "  所有 agent 都可以在此目录读写文件进行协作。\n\n"
    "如果没什么要做的，回复：[SILENT]"
)

_HEARTBEAT_PROMPTS = {"en": _DEFAULT_HEARTBEAT_PROMPT, "zh": _HEARTBEAT_PROMPT_ZH}


class EnvCollector:
    """Collects agent environment snapshots, refreshed by cleanup_loop."""

    def __init__(self, pool: AcpProcessPool | None, agents_cfg: dict, port: int = 18010,
                 client_script: str = "", job_mgr=None, language: str = "en",
                 shared_workdir: str = "/tmp/acp-public"):
        self._pool = pool
        self._agents_cfg = agents_cfg
        self._port = port
        self._client = client_script or str(
            Path(__file__).resolve().parent.parent / "skill" / "scripts" / "acp-client.sh"
        )
        self._job_mgr = job_mgr
        self._language = language
        self._shared_workdir = shared_workdir
        self._snapshot: str = ""
        self._ts: float = 0
        self._enabled_agents: set[str] = set()
        for name, cfg in agents_cfg.items():
            if isinstance(cfg, dict) and cfg.get("heartbeat", False):
                self._enabled_agents.add(name)
        # Heartbeat history — ring buffer, last 50
        self._history: deque[dict] = deque(maxlen=50)
        # Injected contexts — human-in-the-loop directives with TTL
        self._injected_contexts: list[dict] = []
        self.refresh()

    def _agent_profile(self, agent: str) -> dict:
        cfg = self._agents_cfg.get(agent, {})
        if not isinstance(cfg, dict):
            return {}
        meta = cfg.get("metadata", {})
        return {
            "description": cfg.get("description", ""),
            "domains": meta.get("domains", []),
        }

    def refresh(self) -> None:
        agents = {}
        if self._pool:
            for (agent, _), conn in self._pool._connections.items():
                if agent not in agents:
                    agents[agent] = {"busy": 0, "idle": 0, **self._agent_profile(agent)}
                if conn.state == "busy":
                    agents[agent]["busy"] += 1
                else:
                    agents[agent]["idle"] += 1
        for name, cfg in self._agents_cfg.items():
            if not isinstance(cfg, dict) or name in agents:
                continue
            if cfg.get("enabled") and cfg.get("mode") == "acp":
                agents[name] = {"busy": 0, "idle": 0, **self._agent_profile(name)}
        self._snapshot = json.dumps(agents, ensure_ascii=False, separators=(",", ":"))
        self._ts = time.time()

    def is_enabled(self, agent_name: str) -> bool:
        return agent_name in self._enabled_agents

    def get_prefix(self, current_agent: str) -> str:
        if not self.is_enabled(current_agent):
            return ""
        if not self._snapshot or self._snapshot == "{}":
            return ""
        return f"[ENV agents={self._snapshot}]\n\n"

    def get_snapshot(self) -> dict:
        if not self._snapshot:
            return {}
        return {"agents": json.loads(self._snapshot), "ts": self._ts}

    def _build_context(self) -> str:
        lines = []
        # Injected human directives
        remaining = []
        for ctx in self._injected_contexts:
            lines.append(f"  📌 {ctx['text']}")
            ctx["ttl"] -= 1
            if ctx["ttl"] > 0:
                remaining.append(ctx)
        self._injected_contexts = remaining

        if self._job_mgr:
            for j in self._job_mgr.list_jobs(limit=5):
                if j.status == "running":
                    elapsed = round(time.time() - j.created_at)
                    lines.append(f"  🔄 {j.agent} running: {j.prompt[:60]}... ({elapsed}s)")
                elif j.status == "failed":
                    lines.append(f"  ❌ {j.agent} failed: {j.error[:60]}")
                elif j.status == "completed":
                    ago = round(time.time() - j.completed_at)
                    if ago < 300:
                        lines.append(f"  ✅ {j.agent} completed {ago}s ago: {j.prompt[:60]}")
        return "Recent activity:\n" + "\n".join(lines) if lines else "Recent activity: none"

    def build_heartbeat_prompt(self, agent_name: str) -> str:
        self.refresh()
        snapshot = self.get_snapshot()
        agents_info = snapshot.get("agents", {})
        lines = []
        for name, info in agents_info.items():
            marker = " ← you" if name == agent_name else ""
            busy, idle = info.get("busy", 0), info.get("idle", 0)
            if busy and idle:
                state = f"busy({busy}) idle({idle})"
            elif busy:
                state = f"busy({busy})"
            elif idle:
                state = f"idle({idle})"
            else:
                state = "available"
            lines.append(f"  {name}: {state}{marker}")
        agents_json = "\n".join(lines)
        context = self._build_context()

        tpl = get_template("heartbeat", "prompt",
                           _HEARTBEAT_PROMPTS.get(self._language, _DEFAULT_HEARTBEAT_PROMPT))
        return tpl.format(agent_name=agent_name, agents_json=agents_json,
                          port=self._port, client=self._client, context=context,
                          shared_workdir=self._shared_workdir)

    def record(self, agent: str, prompt: str, response: str, silent: bool, duration: float):
        """Record a heartbeat exchange to history."""
        self._history.append({
            "ts": time.time(),
            "agent": agent,
            "prompt": prompt,
            "response": response,
            "silent": silent,
            "duration": round(duration, 1),
        })


def register(app, env_collector: "EnvCollector", pool: AcpProcessPool):
    from starlette.responses import JSONResponse

    @app.get("/heartbeat")
    async def heartbeat_status():
        return JSONResponse({
            "enabled_agents": sorted(env_collector._enabled_agents),
            "snapshot": env_collector.get_snapshot(),
        })

    @app.get("/heartbeat/logs")
    async def heartbeat_logs():
        """View recent heartbeat exchanges."""
        entries = []
        for h in reversed(env_collector._history):
            entries.append({
                "ts": h["ts"],
                "agent": h["agent"],
                "silent": h["silent"],
                "duration": h["duration"],
                "response": h["response"] if not h["silent"] else None,
                "prompt_preview": h["prompt"][:1000],
            })
        return JSONResponse({"total": len(entries), "logs": entries})

    @app.post("/heartbeat/context")
    async def inject_context(req: dict):
        text = req.get("text", "").strip()
        if not text:
            return JSONResponse({"error": "text is required"}, status_code=400)
        ttl = max(1, min(int(req.get("ttl", 3)), 100))
        entry = {"text": text, "ttl": ttl, "created_at": time.time()}
        env_collector._injected_contexts.append(entry)
        log.info("heartbeat_context_injected: ttl=%d text=%s", ttl, text[:80])
        return JSONResponse({"status": "ok", "ttl": ttl, "active_contexts": len(env_collector._injected_contexts)})

    @app.get("/heartbeat/context")
    async def list_contexts():
        return JSONResponse({"contexts": [
            {"text": c["text"], "ttl": c["ttl"], "created_at": c["created_at"]}
            for c in env_collector._injected_contexts
        ]})

    @app.delete("/heartbeat/context")
    async def clear_contexts():
        n = len(env_collector._injected_contexts)
        env_collector._injected_contexts.clear()
        return JSONResponse({"cleared": n})

    @app.post("/heartbeat/{agent_name}")
    async def heartbeat_ping(agent_name: str):
        from .acp_client import AcpError, PoolExhaustedError
        import uuid

        if agent_name not in env_collector._agents_cfg:
            return JSONResponse({"error": f"agent not found: {agent_name}"}, status_code=404)

        cfg = env_collector._agents_cfg.get(agent_name, {})
        if not isinstance(cfg, dict) or cfg.get("mode") != "acp":
            return JSONResponse({"error": f"{agent_name} is not an ACP agent"}, status_code=400)

        prompt = env_collector.build_heartbeat_prompt(agent_name)

        session_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"heartbeat:{agent_name}"))
        try:
            conn = await pool.get_or_create(agent_name, session_id,
                                            cwd=cfg.get("working_dir", "/tmp"))
        except (PoolExhaustedError, AcpError) as e:
            return JSONResponse({"error": str(e)}, status_code=503)

        from .sse import transform_notification
        parts = []
        t0 = time.time()
        try:
            async for notification in conn.session_prompt(prompt):
                if "_prompt_result" in notification:
                    break
                event = transform_notification(notification)
                if event and event["type"] == "message.part":
                    parts.append(event["content"])
        except Exception as e:
            return JSONResponse({"error": str(e)}, status_code=500)

        response = "".join(parts).strip()
        silent = "[SILENT]" in response.upper() or not response
        duration = time.time() - t0

        log.info("heartbeat_ping: agent=%s silent=%s len=%d dur=%.1fs", agent_name, silent, len(response), duration)
        env_collector.record(agent_name, prompt, response, silent, duration)

        return JSONResponse({
            "agent": agent_name,
            "silent": silent,
            "response": response if not silent else None,
            "snapshot": env_collector.get_snapshot(),
        })
