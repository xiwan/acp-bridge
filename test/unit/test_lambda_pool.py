"""Unit tests for src/lambda_pool.py — Lambda burst pool."""

import asyncio
import json
import os
import sys
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

from src.lambda_pool import LambdaPool, LambdaSlot

# ──── Helpers ────


def _make_pool(**kwargs) -> LambdaPool:
    """Create a pool with mocked boto3 client."""
    defaults = {
        "function_name": "test-harness-burst",
        "region": "us-east-1",
        "max_concurrent": 5,
        "timeout": 60,
        "default_model": "bedrock/anthropic.claude-sonnet-4-6",
    }
    defaults.update(kwargs)
    with patch("src.lambda_pool.boto3") as mock_boto:
        mock_client = MagicMock()
        mock_boto.client.return_value = mock_client
        pool = LambdaPool(**defaults)
        pool._client = mock_client
    return pool


def _mock_invoke_success(pool: LambdaPool, output: str = "done", duration: float = 1.5):
    """Configure pool's client to return a successful Lambda response."""
    payload_response = json.dumps(
        {
            "status": "completed",
            "output": output,
            "duration": duration,
            "session_id": "test-session",
        }
    ).encode()
    mock_payload = MagicMock()
    mock_payload.read.return_value = payload_response
    pool._client.invoke.return_value = {
        "StatusCode": 200,
        "Payload": mock_payload,
    }


def _mock_invoke_error(pool: LambdaPool, error_msg: str = "timeout"):
    """Configure pool's client to return a Lambda function error."""
    payload_response = json.dumps(
        {
            "status": "error",
            "error": error_msg,
            "output": "",
            "duration": 5.0,
            "session_id": "test-session",
        }
    ).encode()
    mock_payload = MagicMock()
    mock_payload.read.return_value = payload_response
    pool._client.invoke.return_value = {
        "StatusCode": 200,
        "FunctionError": "Unhandled",
        "Payload": mock_payload,
    }


def _mock_invoke_exception(pool: LambdaPool, exc: Exception):
    """Configure pool's client to raise an exception."""
    pool._client.invoke.side_effect = exc


# ──── Tests: Basic Invoke ────


@pytest.mark.asyncio
async def test_invoke_success():
    """Successful Lambda invocation returns completed status."""
    pool = _make_pool()
    _mock_invoke_success(pool, output="Hello world", duration=2.5)

    result = await pool.invoke(prompt="say hello", profile={"tools": {}})

    assert result["status"] == "completed"
    assert result["output"] == "Hello world"
    assert result["duration"] == 2.5

    # Verify payload sent to Lambda
    call_args = pool._client.invoke.call_args
    assert call_args[1]["FunctionName"] == "test-harness-burst"
    assert call_args[1]["InvocationType"] == "RequestResponse"
    payload = json.loads(call_args[1]["Payload"])
    assert payload["prompt"] == "say hello"
    assert payload["model"] == "bedrock/anthropic.claude-sonnet-4-6"


@pytest.mark.asyncio
async def test_invoke_with_custom_model():
    """Custom model overrides default."""
    pool = _make_pool()
    _mock_invoke_success(pool)

    await pool.invoke(prompt="test", model="bedrock/deepseek.v3.2")

    payload = json.loads(pool._client.invoke.call_args[1]["Payload"])
    assert payload["model"] == "bedrock/deepseek.v3.2"


@pytest.mark.asyncio
async def test_invoke_lambda_function_error():
    """Lambda function error is caught and returned as error status."""
    pool = _make_pool()
    _mock_invoke_error(pool, "out of memory")

    result = await pool.invoke(prompt="big task")

    assert result["status"] == "error"
    assert "out of memory" in result["error"]


@pytest.mark.asyncio
async def test_invoke_exception():
    """Network/boto3 exception returns error result without raising."""
    pool = _make_pool()
    _mock_invoke_exception(pool, RuntimeError("connection timeout"))

    result = await pool.invoke(prompt="test")

    assert result["status"] == "error"
    assert "connection timeout" in result["error"]


# ──── Tests: Capacity ────


@pytest.mark.asyncio
async def test_capacity_limit():
    """Pool rejects when at max_concurrent."""
    pool = _make_pool(max_concurrent=2)

    # Fill pool with fake active slots
    pool._active["s1"] = LambdaSlot(session_id="s1", agent_name="h", profile={})
    pool._active["s2"] = LambdaSlot(session_id="s2", agent_name="h", profile={})

    with pytest.raises(RuntimeError, match="at capacity"):
        await pool.invoke(prompt="overflow")


@pytest.mark.asyncio
async def test_active_tracking():
    """Active slots are tracked during invoke and cleaned up after."""
    pool = _make_pool()
    _mock_invoke_success(pool)

    assert pool.stats["active"] == 0
    result = await pool.invoke(prompt="test")
    assert result["status"] == "completed"
    assert pool.stats["active"] == 0  # cleaned up after


# ──── Tests: Stats ────


@pytest.mark.asyncio
async def test_stats_counting():
    """Stats track total invocations and errors."""
    pool = _make_pool()
    _mock_invoke_success(pool)

    await pool.invoke(prompt="task1")
    await pool.invoke(prompt="task2")

    assert pool.stats["total_invocations"] == 2
    assert pool.stats["total_errors"] == 0

    _mock_invoke_exception(pool, Exception("fail"))
    await pool.invoke(prompt="task3")

    assert pool.stats["total_invocations"] == 3
    assert pool.stats["total_errors"] == 1


# ──── Tests: Batch ────


@pytest.mark.asyncio
async def test_batch_invoke():
    """Batch invokes N lambdas in parallel."""
    pool = _make_pool(max_concurrent=10)
    _mock_invoke_success(pool, output="ok")

    prompts = [{"prompt": f"task-{i}", "session_id": f"sid-{i}"} for i in range(5)]
    results = await pool.invoke_batch(prompts, profile={"tools": {}})

    assert len(results) == 5
    assert all(r["status"] == "completed" for r in results)
    assert pool.stats["total_invocations"] == 5


@pytest.mark.asyncio
async def test_batch_partial_failure():
    """Batch handles partial failures gracefully."""
    pool = _make_pool(max_concurrent=10)
    call_count = {"n": 0}

    def flaky_invoke(payload):
        call_count["n"] += 1
        if call_count["n"] % 2 == 0:
            raise RuntimeError("random failure")
        return {
            "status": "completed",
            "output": "ok",
            "duration": 1.0,
            "session_id": payload["session_id"],
        }

    pool._lambda_invoke = flaky_invoke

    prompts = [{"prompt": f"t{i}"} for i in range(4)]
    results = await pool.invoke_batch(prompts)

    completed = sum(1 for r in results if r["status"] == "completed")
    errors = sum(1 for r in results if r["status"] == "error")
    assert completed == 2
    assert errors == 2


# ──── Tests: Warmup ────


@pytest.mark.asyncio
async def test_warmup():
    """Warmup sends async invocations to pre-warm containers."""
    pool = _make_pool()
    pool._client.invoke.return_value = {"StatusCode": 202}

    result = await pool.warmup(count=10)

    assert result["warmed"] == 10
    assert result["failed"] == 0
    assert pool._client.invoke.call_count == 10
    # Verify async invocation type
    for call in pool._client.invoke.call_args_list:
        assert call[1]["InvocationType"] == "Event"


@pytest.mark.asyncio
async def test_warmup_partial_failure():
    """Warmup handles some invocations failing."""
    pool = _make_pool()
    call_count = {"n": 0}

    def flaky(*args, **kwargs):
        call_count["n"] += 1
        if call_count["n"] % 3 == 0:
            raise Exception("throttled")
        return {"StatusCode": 202}

    pool._client.invoke.side_effect = flaky

    result = await pool.warmup(count=9)
    assert result["warmed"] == 6
    assert result["failed"] == 3


# ──── Tests: Drain ────


@pytest.mark.asyncio
async def test_drain_empty():
    """Drain with no active invocations returns immediately."""
    pool = _make_pool()
    result = await pool.drain()
    assert result["drained"] == 0


@pytest.mark.asyncio
async def test_drain_waits():
    """Drain waits for active invocations to finish."""
    pool = _make_pool(timeout=10)
    pool._active["s1"] = LambdaSlot(session_id="s1", agent_name="h", profile={})

    async def clear_after():
        await asyncio.sleep(0.5)
        pool._active.clear()

    asyncio.create_task(clear_after())
    result = await pool.drain()
    assert result["drained"] == 1
    assert result["remaining"] == 0


# ──── Tests: Security ────


@pytest.mark.asyncio
async def test_no_secrets_in_payload():
    """Lambda payload does not contain API keys (they come from Secrets Manager inside Lambda)."""
    pool = _make_pool()
    _mock_invoke_success(pool)

    await pool.invoke(prompt="test", profile={"agent": {"model": "x"}})

    payload = json.loads(pool._client.invoke.call_args[1]["Payload"])
    # Payload should only have prompt, profile, model, timeout, session_id
    assert "api_key" not in json.dumps(payload)
    assert "litellm_api_key" not in json.dumps(payload)
    assert "secret" not in json.dumps(payload).lower()


# ──── Tests: Regression — capacity control (v0.45.0 audit D2/D3/D4) ────


@pytest.mark.asyncio
async def test_reused_session_id_still_respects_capacity():
    """D2: concurrent invokes sharing one session_id must not bypass max_concurrent.

    Slots were keyed by session_id, so N concurrent calls with the same id
    overwrote one another and len(_active) stayed at 1 — max_concurrent never
    applied. An agent handler deriving a stable per-agent session_id therefore
    had no Bridge-side brake at all.
    """
    pool = _make_pool(max_concurrent=3)
    inflight = {"now": 0, "peak": 0}
    gate = asyncio.Event()

    async def fake_to_thread(fn, payload):
        inflight["now"] += 1
        inflight["peak"] = max(inflight["peak"], inflight["now"])
        await gate.wait()
        inflight["now"] -= 1
        return {
            "status": "completed",
            "output": "ok",
            "duration": 1.0,
            "session_id": payload["session_id"],
        }

    with patch("src.lambda_pool.asyncio.to_thread", fake_to_thread):
        tasks = [
            asyncio.create_task(pool.invoke(prompt=f"p{i}", session_id="one-shared-id"))
            for i in range(10)
        ]
        await asyncio.sleep(0.1)  # let all 10 reach admission
        peak_while_open = inflight["peak"]
        gate.set()
        results = await asyncio.gather(*tasks, return_exceptions=True)

    rejected = sum(1 for r in results if isinstance(r, RuntimeError))
    assert peak_while_open <= 3, f"{peak_while_open} concurrent lambdas exceeded cap of 3"
    assert rejected == 7


@pytest.mark.asyncio
async def test_concurrent_slots_tracked_independently():
    """D2: distinct concurrent invokes each occupy their own slot."""
    pool = _make_pool(max_concurrent=5)
    observed = []
    gate = asyncio.Event()

    async def fake_to_thread(fn, payload):
        observed.append(len(pool._active))
        await gate.wait()
        return {
            "status": "completed",
            "output": "ok",
            "duration": 1.0,
            "session_id": payload["session_id"],
        }

    with patch("src.lambda_pool.asyncio.to_thread", fake_to_thread):
        tasks = [
            asyncio.create_task(pool.invoke(prompt=f"p{i}", session_id="same")) for i in range(3)
        ]
        await asyncio.sleep(0.1)
        assert len(pool._active) == 3, "same session_id must not collapse slots"
        gate.set()
        await asyncio.gather(*tasks)

    assert pool.stats["active"] == 0


@pytest.mark.asyncio
async def test_capacity_admission_is_atomic():
    """D3: capacity check and slot insert happen in one critical section."""
    pool = _make_pool(max_concurrent=2)
    inflight = {"now": 0, "peak": 0}
    gate = asyncio.Event()

    async def fake_to_thread(fn, payload):
        inflight["now"] += 1
        inflight["peak"] = max(inflight["peak"], inflight["now"])
        await gate.wait()
        inflight["now"] -= 1
        return {
            "status": "completed",
            "output": "ok",
            "duration": 1.0,
            "session_id": payload["session_id"],
        }

    with patch("src.lambda_pool.asyncio.to_thread", fake_to_thread):
        tasks = [
            asyncio.create_task(pool.invoke(prompt=f"p{i}", session_id=f"s{i}")) for i in range(20)
        ]
        await asyncio.sleep(0.1)
        peak_while_open = inflight["peak"]
        gate.set()
        await asyncio.gather(*tasks, return_exceptions=True)

    assert peak_while_open <= 2


@pytest.mark.asyncio
async def test_batch_capacity_rejection_preserves_successes():
    """D4: one over-capacity item must not discard the whole batch's results.

    gather(return_exceptions=False) let a RuntimeError propagate out of
    invoke_batch, so a caller lost the output of every invocation that had
    already succeeded.
    """
    pool = _make_pool(max_concurrent=2)
    _mock_invoke_success(pool, output="ok")

    prompts = [{"prompt": f"t{i}"} for i in range(10)]
    results = await pool.invoke_batch(prompts)

    assert len(results) == 10, "one result per input, in order"
    completed = [r for r in results if r["status"] == "completed"]
    errored = [r for r in results if r["status"] == "error"]
    assert completed, "successful invocations must survive"
    assert errored, "rejected invocations reported as errors, not raised"
    assert len(completed) + len(errored) == 10
    assert all("at capacity" in r["error"] for r in errored)


@pytest.mark.asyncio
async def test_batch_result_order_matches_input():
    """D4: normalized error dicts keep positional alignment with the input list."""
    pool = _make_pool(max_concurrent=10)

    def per_prompt(payload):
        if payload["prompt"] == "bad":
            raise RuntimeError("boom")
        return {
            "status": "completed",
            "output": payload["prompt"],
            "duration": 1.0,
            "session_id": payload["session_id"],
        }

    pool._lambda_invoke = per_prompt

    prompts = [{"prompt": "a"}, {"prompt": "bad", "session_id": "sid-bad"}, {"prompt": "c"}]
    results = await pool.invoke_batch(prompts)

    assert [r["status"] for r in results] == ["completed", "error", "completed"]
    assert results[0]["output"] == "a"
    assert results[2]["output"] == "c"
    assert results[1]["session_id"] == "sid-bad"


@pytest.mark.asyncio
async def test_warmup_rejects_non_202_status():
    """Event-type invoke returns 202; anything else is not a warmed container."""
    pool = _make_pool()
    pool._client.invoke.return_value = {"StatusCode": 500}

    result = await pool.warmup(count=4)
    assert result["warmed"] == 0
    assert result["failed"] == 4


def test_pool_init_no_hardcoded_secrets():
    """Pool instance doesn't store any secret values."""
    pool = _make_pool()
    attrs = vars(pool)
    for key, val in attrs.items():
        if isinstance(val, str):
            assert "sk-" not in val, f"Possible secret in pool attribute {key}"
