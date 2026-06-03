"""A2A Mesh routes — L0 (Agent Card + announce) + L1 (POST /a2a invocation).

Only registered when mesh.enabled is true (see main.py wiring), so default
deployments are unaffected.
"""
from fastapi import Request
from fastapi.responses import JSONResponse

from ..mesh import MeshManager
from ..mesh_a2a import A2AAdapter


def register(app, mesh: MeshManager, adapter: A2AAdapter | None = None):

    @app.get("/.well-known/agent.json")
    async def agent_card():
        # Public: capability metadata only, no secrets.
        return mesh.build_agent_card()

    @app.post("/a2a/announce")
    async def announce(request: Request):
        # mesh.token auth (separate from the global Bearer auth_token).
        if mesh.token:
            auth = request.headers.get("authorization", "")
            if auth != f"Bearer {mesh.token}":
                return JSONResponse({"error": "unauthorized"}, status_code=401)
        try:
            body = await request.json()
        except Exception:
            return JSONResponse({"error": "invalid json"}, status_code=400)
        card = body.get("agent_card")
        if not isinstance(card, dict) or not card.get("url"):
            return JSONResponse({"error": "missing agent_card.url"}, status_code=400)
        mesh.record_peer(card, body.get("peers", []))
        return {"agent_card": mesh.build_agent_card(), "peers": mesh.known_peers()}

    @app.post("/a2a")
    async def a2a_rpc(request: Request):
        # L1 invocation entry. mesh.token auth (same plane as /a2a/announce).
        if adapter is None:
            return JSONResponse({"error": "a2a disabled"}, status_code=404)
        if mesh.token:
            if request.headers.get("authorization", "") != f"Bearer {mesh.token}":
                return JSONResponse({"error": "unauthorized"}, status_code=401)
        try:
            rpc = await request.json()
        except Exception:
            return JSONResponse(
                {"jsonrpc": "2.0", "id": None,
                 "error": {"code": -32700, "message": "parse error"}},
                status_code=400)
        return await adapter.dispatch(rpc)

    @app.get("/a2a/peers")
    async def peers():
        # Debug/observability: current peer table.
        return {"self": mesh.self_url, "peers": mesh.peers_view()}
