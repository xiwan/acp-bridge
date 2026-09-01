"""Lambda Pool — serverless burst backend for harness-factory agents.

Implements the same logical interface as AcpProcessPool but executes tasks via
AWS Lambda invocations instead of local subprocesses. Stateless, auto-scaling,
max concurrency controlled by Lambda reserved concurrency.

Usage in config.yaml:
  lambda_pool:
    enabled: true
    function_name: "acp-bridge-harness-burst"
    region: "us-east-1"
    max_concurrent: 100
    timeout: 300
    default_model: "bedrock/anthropic.claude-sonnet-4-6"
"""

import asyncio
import json
import logging
import time
import uuid
from dataclasses import dataclass, field

import boto3
from botocore.config import Config as BotoConfig

log = logging.getLogger("acp-bridge.lambda_pool")


@dataclass
class LambdaSlot:
    """Tracks an in-flight Lambda invocation."""

    session_id: str
    agent_name: str
    profile: dict
    started_at: float = field(default_factory=time.time)
    status: str = "running"  # running | completed | error


class LambdaPool:
    """Manages burst Lambda invocations for harness-factory agents.

    Provides scale-up (pre-warm), invoke (sync), and drain operations.
    Thread-safe via asyncio lock.
    """

    def __init__(
        self,
        function_name: str,
        region: str = "us-east-1",
        max_concurrent: int = 100,
        timeout: int = 300,
        default_model: str = "bedrock/anthropic.claude-sonnet-4-6",
    ):
        self._function_name = function_name
        self._region = region
        self._max_concurrent = max_concurrent
        self._timeout = timeout
        self._default_model = default_model
        self._lock = asyncio.Lock()

        # Track active invocations
        self._active: dict[str, LambdaSlot] = {}
        self._stats_total: int = 0
        self._stats_errors: int = 0

        # Boto3 client with connection pooling
        self._client = boto3.client(
            "lambda",
            region_name=region,
            config=BotoConfig(
                max_pool_connections=max_concurrent,
                retries={"max_attempts": 2, "mode": "adaptive"},
            ),
        )
        log.info(
            "lambda_pool: initialized fn=%s region=%s max=%d timeout=%ds",
            function_name,
            region,
            max_concurrent,
            timeout,
        )

    async def invoke(
        self,
        prompt: str,
        profile: dict | None = None,
        model: str = "",
        timeout: int = 0,
        session_id: str = "",
    ) -> dict:
        """Invoke Lambda synchronously. Returns {status, output, duration, session_id}.

        Raises RuntimeError if pool is at max capacity.
        """
        sid = session_id or str(uuid.uuid4())
        slot = LambdaSlot(
            session_id=sid,
            agent_name="harness-lambda",
            profile=profile or {},
        )

        # Admission + slot insert must be ONE critical section: checking capacity,
        # releasing the lock, then inserting leaves a window where N callers all
        # pass a check that only one of them should have passed.
        #
        # The slot is keyed by a unique invocation id, NOT by session_id: callers
        # legitimately reuse a session_id (a Bridge agent handler derives a stable
        # one per agent), and keying by it would make concurrent invocations
        # overwrite each other's slot so `len(self._active)` stayed at 1 and
        # max_concurrent never applied.
        invocation_id = uuid.uuid4().hex
        async with self._lock:
            if len(self._active) >= self._max_concurrent:
                raise RuntimeError(f"lambda_pool at capacity ({self._max_concurrent})")
            self._active[invocation_id] = slot
            self._stats_total += 1

        payload = {
            "prompt": prompt,
            "profile": profile or {},
            "model": model or self._default_model,
            "timeout": timeout or self._timeout,
            "session_id": sid,
        }

        try:
            result = await asyncio.to_thread(self._lambda_invoke, payload)
            slot.status = result.get("status", "error")
            return result
        except Exception as e:
            async with self._lock:
                self._stats_errors += 1
            slot.status = "error"
            return {
                "status": "error",
                "error": str(e),
                "output": "",
                "duration": round(time.time() - slot.started_at, 2),
                "session_id": sid,
            }
        finally:
            async with self._lock:
                self._active.pop(invocation_id, None)

    async def invoke_batch(
        self,
        prompts: list[dict],
        profile: dict | None = None,
        model: str = "",
    ) -> list[dict]:
        """Invoke N Lambda functions in parallel. Each item in prompts is
        {"prompt": "...", "session_id": "..."} (session_id optional).

        Returns list of results in same order, one per input. A capacity
        rejection (or any other error) for one item becomes an error dict for
        that item only — it never discards the results of items that succeeded.
        """
        tasks = []
        for item in prompts:
            p = item.get("prompt", "") if isinstance(item, dict) else str(item)
            sid = item.get("session_id", "") if isinstance(item, dict) else ""
            tasks.append(self.invoke(prompt=p, profile=profile, model=model, session_id=sid))

        raw = await asyncio.gather(*tasks, return_exceptions=True)

        results: list[dict] = []
        for item, r in zip(prompts, raw, strict=True):
            if isinstance(r, BaseException):
                sid = item.get("session_id", "") if isinstance(item, dict) else ""
                results.append(
                    {
                        "status": "error",
                        "error": str(r),
                        "output": "",
                        "duration": 0,
                        "session_id": sid,
                    }
                )
            else:
                results.append(r)
        return results

    async def warmup(self, count: int = 10) -> dict:
        """Pre-warm Lambda containers by sending lightweight ping invocations.

        Returns {"warmed": N, "failed": N, "duration": seconds}.
        """
        t0 = time.time()
        ping_payload = {
            "prompt": "__warmup__",
            "timeout": 10,
        }

        async def _ping_one():
            try:
                resp = await asyncio.to_thread(self._lambda_invoke_async, ping_payload)
                # Event (async) invoke returns 202 Accepted on success.
                return resp.get("status_code") == 202
            except Exception:
                return False

        results = await asyncio.gather(*[_ping_one() for _ in range(count)])
        warmed = sum(1 for r in results if r)
        failed = count - warmed
        dur = round(time.time() - t0, 2)
        log.info("lambda_pool warmup: %d/%d warmed in %.1fs", warmed, count, dur)
        return {"warmed": warmed, "failed": failed, "duration": dur}

    async def drain(self) -> dict:
        """Wait for all active invocations to complete (no new ones accepted during drain)."""
        async with self._lock:
            active_count = len(self._active)
        if active_count == 0:
            return {"drained": 0}

        log.info("lambda_pool drain: waiting for %d active invocations", active_count)
        # Just wait — Lambda invocations will complete on their own
        deadline = time.time() + self._timeout
        while time.time() < deadline:
            async with self._lock:
                if not self._active:
                    break
            await asyncio.sleep(1.0)

        async with self._lock:
            remaining = len(self._active)
        return {"drained": active_count - remaining, "remaining": remaining}

    @property
    def stats(self) -> dict:
        return {
            "function_name": self._function_name,
            "region": self._region,
            "max_concurrent": self._max_concurrent,
            "active": len(self._active),
            "total_invocations": self._stats_total,
            "total_errors": self._stats_errors,
        }

    def _lambda_invoke(self, payload: dict) -> dict:
        """Synchronous Lambda Invoke (runs in thread)."""
        resp = self._client.invoke(
            FunctionName=self._function_name,
            InvocationType="RequestResponse",
            Payload=json.dumps(payload).encode(),
        )
        status_code = resp["StatusCode"]
        body = resp["Payload"].read().decode()

        if status_code != 200:
            error_msg = resp.get("FunctionError", f"HTTP {status_code}")
            raise RuntimeError(f"Lambda error: {error_msg} - {body[:200]}")

        try:
            result = json.loads(body)
        except json.JSONDecodeError:
            raise RuntimeError(f"Lambda returned non-JSON: {body[:200]}")

        # Check for Lambda-level function error
        if resp.get("FunctionError"):
            raise RuntimeError(f"Lambda function error: {result.get('errorMessage', body[:200])}")

        return result

    def _lambda_invoke_async(self, payload: dict) -> dict:
        """Async (fire-and-forget) Lambda Invoke for warmup."""
        resp = self._client.invoke(
            FunctionName=self._function_name,
            InvocationType="Event",  # async, returns 202 immediately
            Payload=json.dumps(payload).encode(),
        )
        return {"status_code": resp["StatusCode"]}
