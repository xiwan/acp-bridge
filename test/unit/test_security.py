"""Security middleware policy tests."""

import logging

import httpx
import pytest
from fastapi import FastAPI

from main import setup_logging
from src.security import LOCAL_ONLY_PATHS, NO_AUTH_PATHS, SecurityMiddleware


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
            "/protected",
            headers={"Authorization": "Bearer unit"},
        )

    assert protected.status_code == 401
    assert authorized.status_code == 200
    assert {"/live", "/ready", "/health"}.issubset(NO_AUTH_PATHS)


def test_empty_auth_token_is_rejected():
    app = FastAPI()
    with pytest.raises(ValueError, match="must not be empty"):
        SecurityMiddleware(app, [], "")


def test_verbose_logging_suppresses_credential_bearing_aws_sdk_logs():
    setup_logging(verbose=True)
    for logger_name in ("boto3", "botocore", "s3transfer"):
        assert logging.getLogger(logger_name).level >= logging.WARNING


@pytest.mark.asyncio
async def test_downloads_require_bearer_token():
    app = FastAPI()

    async def ok():
        return {"ok": True}

    app.add_api_route("/files/report/download", ok, methods=["GET"])
    options = {"allowed_ips": [], "auth_token": "unit", "rate_limit": 0}
    app.add_middleware(SecurityMiddleware, **options)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        denied = await client.get("/files/report/download")
        allowed = await client.get(
            "/files/report/download",
            headers={"Authorization": "Bearer unit"},
        )
    assert denied.status_code == 401
    assert allowed.status_code == 200


@pytest.mark.asyncio
async def test_internal_callback_is_loopback_only():
    app = FastAPI()

    async def ok():
        return {"ok": True}

    app.add_api_route("/internal/llm-callback", ok, methods=["POST"])
    options = {"allowed_ips": [], "auth_token": "unit", "rate_limit": 0}
    app.add_middleware(SecurityMiddleware, **options)
    remote = httpx.ASGITransport(app=app, client=("203.0.113.10", 1234))
    async with httpx.AsyncClient(transport=remote, base_url="http://test") as client:
        denied = await client.post("/internal/llm-callback")
    local = httpx.ASGITransport(app=app, client=("127.0.0.1", 1234))
    async with httpx.AsyncClient(transport=local, base_url="http://test") as client:
        allowed = await client.post("/internal/llm-callback")
    assert denied.status_code == 403
    assert allowed.status_code == 200
    assert "/internal/llm-callback" in LOCAL_ONLY_PATHS
