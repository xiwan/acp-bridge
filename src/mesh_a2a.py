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
    parts = [MessagePart(content=p.get("text", ""), content_type="text/plain")
             for p in message.get("parts", []) if p.get("text")]
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

    def __init__(self, agents_provider, job_mgr=None, remote_skills=None):
        # agents_provider: callable -> {name: Agent}; deferred so app.state is ready.
        self._agents_provider = agents_provider
        self._job_mgr = job_mgr
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
            return _rpc_error(rpc_id, -32011,
                              f"hop limit: {skill} is remote, refusing 2nd hop")
        agents = self._agents()
        agent = agents.get(skill)
        if agent is None:
            return _rpc_error(rpc_id, -32601, f"unknown skill: {skill}")
        try:
            text = await _drain(agent, _a2a_parts_to_acp(params.get("message") or {}))
        except Exception as e:
            log.warning("a2a tasks/send failed skill=%s err=%s", skill, e)
            return _rpc_error(rpc_id, -32000, f"agent error: {e}")
        return _rpc_result(rpc_id, {
            "status": {"state": "completed"},
            "artifacts": [{"parts": [{"type": "text", "text": text}]}],
            "metadata": {"usage": None, "cost": dict(FREE_COST)},
        })

    def _tasks_get(self, rpc_id, params: dict) -> dict:
        task_id = params.get("id") or params.get("task_id")
        if not self._job_mgr or not task_id:
            return _rpc_error(rpc_id, -32602, "tasks/get requires a known task id")
        job = self._job_mgr.get(task_id)
        if job is None:
            return _rpc_error(rpc_id, -32001, f"task not found: {task_id}")
        state = "completed" if job.status == "completed" else (
            "failed" if job.status == "failed" else "working")
        result = {"id": task_id, "status": {"state": state}}
        if getattr(job, "result", None):
            result["artifacts"] = [{"parts": [{"type": "text", "text": job.result}]}]
        return _rpc_result(rpc_id, result)
