"""A2A Mesh L1 — A2A Server adapter (JSON-RPC 2.0 ↔ ACP).

Translates incoming A2A `tasks/send` / `tasks/get` calls into the existing ACP
agent handler path. No new execution logic: each local agent is invoked via its
already-registered handler (app.state.acp_agents[name].run), which carries
fallback + circuit-breaker behaviour. The ACP handler ignores its `context`
argument, so we pass None and avoid constructing the heavy SDK Context.

Billing is reserved but free in L1: responses carry metadata.usage/cost placeholders.
"""

import logging

from acp_sdk.models import Message, MessagePart

from .url_safety import UnsafeUrlError, validate_outbound_url

log = logging.getLogger("acp-bridge.mesh.a2a")

FREE_COST = {"amount": 0, "currency": "USD"}


def _rpc_error(rpc_id, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": rpc_id, "error": {"code": code, "message": message}}


def _rpc_result(rpc_id, result: dict) -> dict:
    return {"jsonrpc": "2.0", "id": rpc_id, "result": result}


def _a2a_parts_to_acp(message: dict) -> list[Message]:
    """A2A message.parts[*].text -> ACP Message list (text only).

    cwd/session are not part of the A2A message shape; remote callers get a
    fresh per-agent session on this node. Local invocation paths (/runs, /jobs)
    remain the way to pin cwd/session.
    """
    parts = [
        MessagePart(content=p.get("text", ""), content_type="text/plain")
        for p in message.get("parts", [])
        if p.get("text")
    ]
    return [Message(parts=parts)]


async def _drain(agent, input: list[Message]) -> str:
    """Run the ACP handler (context ignored) and concatenate text output."""
    out: list[str] = []
    async for y in agent.run(input, None):
        if isinstance(y, Message):
            out += [p.content for p in y.parts if p.content]
        elif isinstance(y, MessagePart):
            if y.content:
                out.append(y.content)
        elif isinstance(y, str):
            out.append(y)
    return "".join(out)


class A2AAdapter:
    """Dispatches A2A JSON-RPC methods against this node's local agents + job store."""

    def __init__(
        self,
        agents_provider,
        job_mgr=None,
        remote_skills=None,
        pool=None,
        allowed_private_targets: frozenset[str] = frozenset(),
    ):
        # agents_provider: callable -> {name: Agent}; deferred so app.state is ready.
        self._agents_provider = agents_provider
        self._job_mgr = job_mgr
        self._pool = pool  # L3: needed to run a local agent with an explicit cwd
        self._allowed_private_targets = allowed_private_targets
        # L2: names registered as a2a-remote handlers (forward to a peer). Used to
        # enforce the 1-hop limit: an inbound hopped request must not re-forward.
        self.remote_skills = remote_skills if remote_skills is not None else set()

    def _agents(self) -> dict:
        try:
            return self._agents_provider() or {}
        except Exception:
            return {}

    async def dispatch(self, rpc: dict, inbound_hop: bool = False) -> dict:
        rpc_id = rpc.get("id")
        method = rpc.get("method")
        params = rpc.get("params") or {}
        if method == "tasks/send":
            return await self._tasks_send(rpc_id, params, inbound_hop=inbound_hop)
        if method == "tasks/get":
            return self._tasks_get(rpc_id, params)
        return _rpc_error(rpc_id, -32601, f"method not found: {method}")

    async def _tasks_send(self, rpc_id, params: dict, inbound_hop: bool = False) -> dict:
        skill = params.get("skill") or params.get("agent")
        # 1-hop limit: refuse to forward a remote skill for an already-hopped request.
        if inbound_hop and skill in self.remote_skills:
            return _rpc_error(rpc_id, -32011, f"hop limit: {skill} is remote, refusing 2nd hop")
        agents = self._agents()
        agent = agents.get(skill)
        if agent is None:
            return _rpc_error(rpc_id, -32601, f"unknown skill: {skill}")

        # L3: workspace relay — if the caller staged a workspace, run the agent in it.
        ws_in = params.get("workspace_in_url")
        ws_out = params.get("workspace_out_url")
        if ws_in:
            return await self._tasks_send_workspace(rpc_id, skill, params, ws_in, ws_out)

        try:
            text = await _drain(agent, _a2a_parts_to_acp(params.get("message") or {}))
        except Exception as e:
            log.warning("a2a tasks/send failed skill=%s err=%s", skill, e)
            return _rpc_error(rpc_id, -32000, f"agent error: {e}")
        return _rpc_result(
            rpc_id,
            {
                "status": {"state": "completed"},
                "artifacts": [{"parts": [{"type": "text", "text": text}]}],
                "metadata": {"usage": None, "cost": dict(FREE_COST)},
            },
        )

    async def _tasks_send_workspace(self, rpc_id, skill, params, ws_in, ws_out) -> dict:
        """L3 (B side): download workspace → run agent with that cwd → upload result."""
        if self._pool is None:
            return _rpc_error(rpc_id, -32010, "workspace step requires a process pool")
        # Validate before anything else — including the imports below, which
        # pull in the agent-execution machinery. Untrusted ws_in/ws_out never
        # get that far if they're unsafe. Each SafeTarget pins the exact IP
        # validated here; the httpx calls below connect to that IP directly
        # rather than re-resolving the hostname, closing the DNS-rebinding
        # TOCTOU a separate validate-then-fetch would leave open.
        try:
            ws_in_target = validate_outbound_url(
                ws_in, allowed_targets=self._allowed_private_targets
            )
            ws_out_target = (
                validate_outbound_url(ws_out, allowed_targets=self._allowed_private_targets)
                if ws_out
                else None
            )
        except UnsafeUrlError as e:
            return _rpc_error(rpc_id, -32014, f"unsafe workspace url: {e}")
        import asyncio
        import tempfile
        import uuid

        import httpx

        from src import s3 as _s3
        from src.agents import _call_acp_agent_internal

        prompt = "".join(p.get("text", "") for p in (params.get("message") or {}).get("parts", []))
        tmp = tempfile.mkdtemp(prefix="mesh-ws-")

        # httpx's module-level get/put accept no `extensions`, so pinning
        # requires a real Client. follow_redirects stays off so a 30x can't
        # escape the validated IP — see url_safety.py. The transfers and the
        # tar (un)pack run in a thread: a slow peer or big workspace must not
        # stall the event loop (up to timeout=120s).
        def _download():
            with httpx.Client(timeout=120, follow_redirects=False) as c:
                r = c.get(
                    ws_in_target.pinned_url,
                    headers={"Host": ws_in_target.host_header},
                    extensions={"sni_hostname": ws_in_target.host},
                )
            r.raise_for_status()
            _s3.unpack_dir(r.content, tmp)

        try:
            await asyncio.to_thread(_download)
        except Exception as e:
            return _rpc_error(rpc_id, -32012, f"workspace download failed: {e}")
        session_id = str(uuid.uuid4())
        parts: list[str] = []
        try:
            async for y in _call_acp_agent_internal(
                agent_name=skill,
                prompt=prompt,
                pool=self._pool,
                profile=None,
                session_id=session_id,
                cwd=tmp,
                enrich_prompt=False,
            ):
                if getattr(y, "content", ""):
                    parts.append(y.content)
        except Exception as e:
            log.warning("a2a workspace step failed skill=%s err=%s", skill, e)
            return _rpc_error(rpc_id, -32000, f"agent error: {e}")
        if ws_out_target:

            def _upload():
                with httpx.Client(timeout=120, follow_redirects=False) as c:
                    c.put(
                        ws_out_target.pinned_url,
                        content=_s3.pack_dir(tmp),
                        headers={"Host": ws_out_target.host_header},
                        extensions={"sni_hostname": ws_out_target.host},
                    ).raise_for_status()

            try:
                await asyncio.to_thread(_upload)
            except Exception as e:
                return _rpc_error(rpc_id, -32013, f"workspace upload failed: {e}")
        return _rpc_result(
            rpc_id,
            {
                "status": {"state": "completed"},
                "artifacts": [{"parts": [{"type": "text", "text": "".join(parts)}]}],
                "metadata": {"usage": None, "cost": dict(FREE_COST)},
            },
        )

    def _tasks_get(self, rpc_id, params: dict) -> dict:
        task_id = params.get("id") or params.get("task_id")
        if not self._job_mgr or not task_id:
            return _rpc_error(rpc_id, -32602, "tasks/get requires a known task id")
        job = self._job_mgr.get(task_id)
        if job is None:
            return _rpc_error(rpc_id, -32001, f"task not found: {task_id}")
        state = (
            "completed"
            if job.status == "completed"
            else ("failed" if job.status == "failed" else "working")
        )
        result = {"id": task_id, "status": {"state": state}}
        if getattr(job, "result", None):
            result["artifacts"] = [{"parts": [{"type": "text", "text": job.result}]}]
        return _rpc_result(rpc_id, result)
