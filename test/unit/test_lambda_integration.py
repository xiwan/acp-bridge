"""Integration test for Lambda Pool — end-to-end via /lambda-pool/invoke.

This test mocks the boto3 Lambda client to simulate Lambda invocations
without requiring actual AWS infrastructure. It verifies the full request
path: HTTP → route → LambdaPool → (mocked) Lambda → response.
"""

import json
import os
import sys
from unittest.mock import MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))


def _make_lambda_response(status="completed", output="Hello from Lambda!", duration=2.5):
    """Build a mock Lambda Invoke response."""
    payload = json.dumps(
        {
            "status": status,
            "output": output,
            "duration": duration,
            "session_id": "test-sid",
        }
    ).encode()
    mock_payload = MagicMock()
    mock_payload.read.return_value = payload
    return {
        "StatusCode": 200,
        "Payload": mock_payload,
    }


@pytest.fixture
def mock_boto3():
    """Patch boto3 globally to mock Lambda client."""
    with patch("src.lambda_pool.boto3") as mock:
        mock_client = MagicMock()
        mock.client.return_value = mock_client
        mock_client.invoke.return_value = _make_lambda_response()
        yield mock_client


@pytest.fixture
def app_with_lambda(mock_boto3):
    """Create a minimal ACP Bridge app with lambda_pool enabled."""
    from acp_sdk.server import Server
    from acp_sdk.server.app import create_app

    from src.lambda_pool import LambdaPool
    from src.routes import lambda_pool as lambda_pool_routes

    # Create pool (boto3 is already mocked)
    pool = LambdaPool(
        function_name="test-fn",
        region="us-east-1",
        max_concurrent=10,
        timeout=60,
    )
    pool._client = mock_boto3

    # Minimal app
    server = Server()
    app = create_app(*server.agents)
    lambda_pool_routes.register(app, pool)

    return app, pool, mock_boto3


@pytest.mark.asyncio
async def test_invoke_endpoint(app_with_lambda):
    """POST /lambda-pool/invoke → 200 with agent output."""
    app, pool, mock_client = app_with_lambda

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/lambda-pool/invoke",
            json={
                "prompt": "Write a hello world script",
                "profile": {"tools": {"fs": {"permissions": ["read", "write"]}}},
                "model": "bedrock/deepseek.v3.2",
            },
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "completed"
    assert body["output"] == "Hello from Lambda!"

    # Verify Lambda was called with correct payload
    call_kwargs = mock_client.invoke.call_args[1]
    assert call_kwargs["FunctionName"] == "test-fn"
    payload = json.loads(call_kwargs["Payload"])
    assert payload["prompt"] == "Write a hello world script"
    assert payload["model"] == "bedrock/deepseek.v3.2"
    assert payload["profile"]["tools"]["fs"]["permissions"] == ["read", "write"]


@pytest.mark.asyncio
async def test_invoke_error(app_with_lambda):
    """Lambda returning error → 502."""
    app, pool, mock_client = app_with_lambda
    mock_client.invoke.return_value = _make_lambda_response(status="error", output="", duration=1.0)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/lambda-pool/invoke",
            json={
                "prompt": "bad task",
            },
        )

    assert resp.status_code == 502
    body = resp.json()
    assert body["status"] == "error"


@pytest.mark.asyncio
async def test_invoke_empty_prompt(app_with_lambda):
    """Empty prompt → 400."""
    app, pool, mock_client = app_with_lambda

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/lambda-pool/invoke",
            json={
                "prompt": "",
            },
        )

    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_status_endpoint(app_with_lambda):
    """GET /lambda-pool/status returns pool stats."""
    app, pool, mock_client = app_with_lambda

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/lambda-pool/status")

    assert resp.status_code == 200
    body = resp.json()
    assert body["function_name"] == "test-fn"
    assert body["max_concurrent"] == 10
    assert body["active"] == 0


@pytest.mark.asyncio
async def test_batch_endpoint(app_with_lambda):
    """POST /lambda-pool/invoke-batch invokes N lambdas in parallel."""
    app, pool, mock_client = app_with_lambda

    prompts = [{"prompt": f"task-{i}"} for i in range(5)]

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/lambda-pool/invoke-batch",
            json={
                "prompts": prompts,
                "model": "bedrock/anthropic.claude-sonnet-4-6",
            },
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 5
    assert body["completed"] == 5
    assert body["failed"] == 0
    assert len(body["results"]) == 5

    # Lambda should have been called 5 times
    assert mock_client.invoke.call_count == 5


@pytest.mark.asyncio
async def test_scale_endpoint(app_with_lambda):
    """POST /lambda-pool/scale pre-warms Lambda containers."""
    app, pool, mock_client = app_with_lambda
    mock_client.invoke.return_value = {"StatusCode": 202}

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/lambda-pool/scale", json={"count": 8})

    assert resp.status_code == 200
    body = resp.json()
    assert body["warmed"] == 8
    assert body["failed"] == 0
    # All invocations should be Event type (async warmup)
    for call in mock_client.invoke.call_args_list:
        assert call[1]["InvocationType"] == "Event"


@pytest.mark.asyncio
async def test_drain_endpoint(app_with_lambda):
    """POST /lambda-pool/drain with no active invocations."""
    app, pool, mock_client = app_with_lambda

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/lambda-pool/drain")

    assert resp.status_code == 200
    body = resp.json()
    assert body["drained"] == 0


@pytest.mark.asyncio
async def test_lambda_handler_disabled():
    """When lambda_pool is not enabled, routes return 503."""
    from acp_sdk.server import Server
    from acp_sdk.server.app import create_app

    from src.routes import lambda_pool as lambda_pool_routes

    server = Server()
    app = create_app(*server.agents)
    lambda_pool_routes.register(app, None)  # disabled

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/lambda-pool/status")
        assert resp.status_code == 503

        resp = await client.post("/lambda-pool/invoke", json={"prompt": "test"})
        assert resp.status_code == 503

        resp = await client.post("/lambda-pool/scale", json={"count": 5})
        assert resp.status_code == 503


@pytest.mark.asyncio
async def test_no_secrets_leak_in_response(app_with_lambda):
    """Response body never contains API keys or secrets."""
    app, pool, mock_client = app_with_lambda

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/lambda-pool/invoke",
            json={
                "prompt": "test secret check",
            },
        )

    body_str = resp.text
    assert "sk-" not in body_str
    assert "api_key" not in body_str.lower()
    assert "secret" not in body_str.lower()


# ──── Tests: Regression — agent registration (v0.45.0 audit D1) ────


def _live_agents(app):
    """Extract the SDK's internal agent dict, the way main.py does."""
    for route in app.routes:
        if getattr(route, "name", None) == "list_agents":
            for cell in route.endpoint.__closure__:
                try:
                    val = cell.cell_contents
                    if isinstance(val, dict) and all(hasattr(v, "name") for v in val.values()):
                        return val
                except ValueError:
                    pass
    return None


@pytest.mark.asyncio
async def test_lambda_agent_reaches_http_surface(mock_boto3):
    """D1: a pool="lambda" agent must be visible to /runs.

    main.py snapshots server.agents in create_app(), so registering a deferred
    lambda agent on the Server afterwards put it in a list nobody reads — the
    agent existed in config and in logs but /runs never saw it. Registration
    must insert the manifest into the live dict the SDK serves (the pattern
    src/routes/harness.py already uses).
    """
    from acp_sdk.models import MessagePart
    from acp_sdk.server import Server
    from acp_sdk.server.app import create_app

    from src.agents import make_lambda_agent_handler
    from src.lambda_pool import LambdaPool

    async def local_handler(input, context):
        yield MessagePart(content="local", content_type="text/plain")

    server = Server()
    server.agent(name="kiro", description="local agent")(local_handler)

    # main.py: create_app() snapshots agents here
    app = create_app(*server.agents)
    live = _live_agents(app)
    assert live is not None, "SDK agents dict must be discoverable"
    assert set(live) == {"kiro"}

    # main.py: deferred lambda agent registration happens after create_app()
    pool = LambdaPool(function_name="test-fn", max_concurrent=10)
    pool._client = mock_boto3
    handler = make_lambda_agent_handler("harness-burst", pool, profile={}, model="m")

    _srv = Server()
    _srv.agent(name="harness-burst", description="lambda burst")(handler)
    live[_srv.agents[0].name] = _srv.agents[0]

    assert "harness-burst" in live, "lambda agent must reach the HTTP surface"
    assert live["harness-burst"].description == "lambda burst"

    # And the SDK actually advertises it
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/agents")
    assert resp.status_code == 200
    names = {a["name"] for a in resp.json()["agents"]}
    assert "harness-burst" in names, f"/agents advertises {names}"


@pytest.mark.asyncio
async def test_lambda_handler_unique_session_per_call(mock_boto3):
    """D2: the handler must not reuse one session_id across concurrent calls."""
    from acp_sdk.models import Message, MessagePart

    from src.agents import make_lambda_agent_handler
    from src.lambda_pool import LambdaPool

    pool = LambdaPool(function_name="test-fn", max_concurrent=10)
    pool._client = mock_boto3

    seen_sids = []

    async def capture(prompt, profile, model, session_id):
        seen_sids.append(session_id)
        return {"status": "completed", "output": "ok", "duration": 1.0, "session_id": session_id}

    pool.invoke = capture
    handler = make_lambda_agent_handler("harness-burst", pool)

    msg = Message(parts=[MessagePart(content="hi", content_type="text/plain")])
    for _ in range(3):
        async for _part in handler([msg], None):
            pass

    assert len(set(seen_sids)) == 3, f"session ids must be unique, got {seen_sids}"
