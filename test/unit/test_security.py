"""Security middleware policy tests."""

import httpx
import pytest
from fastapi import FastAPI

from src.security import NO_AUTH_PATHS, SecurityMiddleware


@pytest.mark.asyncio
async def test_probe_endpoints_do_not_require_bearer_token():
    app = FastAPI()

    async def ok():
        return {"ok": True}

    for path in ("/live", "/ready", "/health"):
        app.add_api_route(path, ok, methods=["GET"])
    app.add_api_route("/protected", ok, methods=["GET"])
    middleware_options = {
        "allowed_ips": [],
        "auth_token": "unit",
        "rate_limit": 0,
    }
    app.add_middleware(SecurityMiddleware, **middleware_options)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        for path in ("/live", "/ready", "/health"):
            response = await client.get(path)
            assert response.status_code == 200
        protected = await client.get("/protected")
        authorized = await client.get(
            "/protected", headers={"Authorization": "Bearer unit"},
        )

    assert protected.status_code == 401
    assert authorized.status_code == 200
    assert {"/live", "/ready", "/health"}.issubset(NO_AUTH_PATHS)
