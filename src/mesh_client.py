"""A2A Mesh L2 — A2A Client + mesh routing.

Lets this node invoke a remote peer's agents. For each skill a peer has but this
node lacks, a `mode=a2a-remote` handler is registered into app.state.acp_agents,
so routing is transparent to /runs, /jobs, /pipelines and the fallback framework.

The remote handler matches make_acp_agent_handler's signature
(input: list[Message], context) -> AsyncGenerator[MessagePart]) and forwards via
the L1 POST /a2a `tasks/send` protocol with `X-A2A-Hop: 1` (1-hop limit) and mesh.token.

Local agents always take priority: remote handlers are only registered for skills
not present locally (see reconcile()).
"""
import logging
from collections.abc import AsyncGenerator

import httpx
from acp_sdk.models import Message, MessagePart

log = logging.getLogger("acp-bridge.mesh.client")


def make_a2a_remote_handler(agent_name: str, peer_url: str, mesh_token: str):
    """Return a transparent handler forwarding to a peer's POST /a2a (tasks/send)."""
    target = peer_url.rstrip("/") + "/a2a"

    async def handler(input: list[Message], context) -> AsyncGenerator[MessagePart, None]:
        prompt = "".join(p.content for m in input for p in m.parts if p.content)
        body = {"jsonrpc": "2.0", "id": 1, "method": "tasks/send",
                "params": {"skill": agent_name,
                           "message": {"parts": [{"type": "text", "text": prompt}]}}}
        headers = {"X-A2A-Hop": "1"}
        if mesh_token:
            headers["Authorization"] = f"Bearer {mesh_token}"
        try:
            async with httpx.AsyncClient(timeout=300) as c:
                r = await c.post(target, json=body, headers=headers)
                r.raise_for_status()
                resp = r.json()
        except Exception as e:
            log.warning("a2a remote call failed agent=%s peer=%s err=%s",
                        agent_name, peer_url, e)
            yield MessagePart(content=f"[remote error] {agent_name}@{peer_url}: {e}",
                              content_type="text/plain")
            return
        if "error" in resp:
            yield MessagePart(content=f"[remote error] {resp['error'].get('message')}",
                              content_type="text/plain")
            return
        for art in resp.get("result", {}).get("artifacts", []):
            for part in art.get("parts", []):
                if part.get("text"):
                    yield MessagePart(content=part["text"], content_type="text/plain")

    return handler


def reconcile(app, mesh, remote_skills=None) -> list[str]:
    """Register remote handlers for peer-only skills; return newly registered names.

    Local agents win: a skill present locally is never shadowed by a remote one.
    Reuses the harness route's dynamic-registration pattern. If `remote_skills` (a
    set) is given, registered names are added to it (used for the 1-hop limit).
    """
    acp_agents = getattr(app.state, "acp_agents", None)
    if acp_agents is None:
        return []

    local = mesh._agent_names()
    # pick one healthy peer per skill (local-priority: skip skills we already serve)
    skill_to_peer: dict[str, str] = {}
    peer_by_url = {p.url: p for p in mesh._peers.values()}
    for p in mesh._peers.values():
        if not p.healthy:
            continue
        for skill in p.skills:
            if skill in local or (skill in acp_agents and skill not in (remote_skills or set())):
                continue
            skill_to_peer.setdefault(skill, p.url)

    from acp_sdk.server import Server
    added: list[str] = []
    for skill, peer_url in skill_to_peer.items():
        if skill in acp_agents and remote_skills is not None and skill in remote_skills:
            continue  # already registered as remote
        peer = peer_by_url.get(peer_url)
        node = (peer.node_name if peer and peer.node_name else peer_url)
        info = (peer.skill_info.get(skill, {}) if peer else {})
        real_desc = info.get("description") or f"{skill} agent"
        # Description carries the human-readable location: "<real desc> (via mesh@<node>)"
        description = f"{real_desc} (via mesh@{node})"
        # Structured location marker on metadata.tags: "mesh" + "node:<name>" + peer's tags.
        # Metadata must be set at construction time (Agent.metadata is read-only).
        metadata = None
        try:
            from acp_sdk.models.models import Metadata
            peer_tags = info.get("tags") or []
            metadata = Metadata(
                tags=["mesh", f"node:{node}", f"peer:{peer_url}"] + list(peer_tags))
        except Exception as e:
            log.warning("mesh L2: could not build location metadata for %s: %s", skill, e)
        handler = make_a2a_remote_handler(skill, peer_url, mesh.token)
        srv = Server()
        srv.agent(name=skill, description=description, metadata=metadata)(handler)
        acp_agents[skill] = srv.agents[0]
        if remote_skills is not None:
            remote_skills.add(skill)
        added.append(skill)
    if added:
        log.info("mesh L2: registered remote agents=%s", added)
    return added
