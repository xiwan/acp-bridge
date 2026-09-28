"""Router routes — dry-run a Jev routing decision and inspect router status.

Both endpoints sit behind the global Bearer middleware (not in NO_AUTH_PATHS).
When the router is disabled they answer 503 so callers can feature-detect.
"""

import logging

from fastapi import Request
from fastapi.responses import JSONResponse

from ..jev_router import JevRouter

log = logging.getLogger("acp-bridge.routes.router")

MAX_PREVIEW_PROMPT = 20000


def register(app, router: JevRouter | None):
    """Register /route/* endpoints."""

    if router is None:

        @app.get("/route/status")
        async def route_status_disabled():
            return JSONResponse({"enabled": False, "error": "router not enabled"}, status_code=503)

        @app.post("/route/preview")
        async def route_preview_disabled(request: Request):
            return JSONResponse({"enabled": False, "error": "router not enabled"}, status_code=503)

        return

    @app.get("/route/status")
    async def route_status():
        """Router config + counters. Never includes the API key."""
        return router.status()

    @app.post("/route/preview")
    async def route_preview(request: Request):
        """Ask Jev which agent would run this prompt, without executing anything.

        Body: {"prompt": "..."}
        """
        try:
            body = await request.json()
        except Exception:
            return JSONResponse({"error": "invalid json"}, status_code=400)
        prompt = body.get("prompt") if isinstance(body, dict) else None
        if not isinstance(prompt, str) or not prompt.strip():
            return JSONResponse({"error": "prompt is required"}, status_code=400)
        if len(prompt) > MAX_PREVIEW_PROMPT:
            return JSONResponse(
                {"error": f"prompt exceeds {MAX_PREVIEW_PROMPT} chars"}, status_code=413
            )
        decision = await router.decide(prompt)
        out = decision.to_dict()
        out["router"] = router.agent_name
        return out
