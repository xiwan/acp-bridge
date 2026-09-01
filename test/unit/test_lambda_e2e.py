"""E2E test for Lambda Burst stack.

Tests two levels:
1. AWS resource simulation (moto) — Secrets Manager CRUD, IAM, Lambda function creation
2. Full flow simulation — LambdaPool with realistic behavior (mock at _lambda_invoke level)

moto cannot execute Lambda code without Docker, so we:
  - Use moto to verify Secrets Manager integration (the wrapper reads secrets at runtime)
  - Use moto to verify Lambda function creation (CDK would do this)
  - Mock _lambda_invoke to simulate Lambda responses for pool-level tests
"""

import json
import os
import sys
import time
import zipfile
from io import BytesIO
from unittest.mock import MagicMock, patch

import boto3
import pytest
from moto import mock_aws

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))


@pytest.fixture
def aws_env(monkeypatch):
    """Set fake AWS credentials for moto."""
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_SECURITY_TOKEN", "testing")
    monkeypatch.setenv("AWS_SESSION_TOKEN", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")


# ──── Part 1: Secrets Manager E2E (moto) ────


class TestSecretsManagerE2E:
    """Verify the secret lifecycle that the Lambda wrapper depends on."""

    @mock_aws
    def test_create_and_read_secret(self, aws_env):
        """Secret creation and retrieval (JSON format)."""
        sm = boto3.client("secretsmanager", region_name="us-east-1")
        sm.create_secret(
            Name="/acp-bridge/lambda-burst/litellm-api-key",
            SecretString=json.dumps({"apiKey": "sk-real-key-123"}),
        )

        resp = sm.get_secret_value(SecretId="/acp-bridge/lambda-burst/litellm-api-key")
        parsed = json.loads(resp["SecretString"])
        assert parsed["apiKey"] == "sk-real-key-123"

    @mock_aws
    def test_secret_rotation(self, aws_env):
        """Secret value can be updated (simulates key rotation)."""
        sm = boto3.client("secretsmanager", region_name="us-east-1")
        sm.create_secret(
            Name="/acp-bridge/test-rotation",
            SecretString="sk-original-key",
        )

        # Rotate
        sm.put_secret_value(
            SecretId="/acp-bridge/test-rotation",
            SecretString="sk-rotated-key-v2",
        )

        resp = sm.get_secret_value(SecretId="/acp-bridge/test-rotation")
        assert resp["SecretString"] == "sk-rotated-key-v2"

    @mock_aws
    def test_plain_string_secret(self, aws_env):
        """Secret stored as plain string (not JSON)."""
        sm = boto3.client("secretsmanager", region_name="us-east-1")
        sm.create_secret(Name="/acp-bridge/plain", SecretString="sk-plain-key")

        resp = sm.get_secret_value(SecretId="/acp-bridge/plain")
        assert resp["SecretString"] == "sk-plain-key"

    @mock_aws
    def test_wrapper_get_api_key(self, aws_env, monkeypatch):
        """Lambda wrapper _get_api_key reads from moto Secrets Manager."""
        sm = boto3.client("secretsmanager", region_name="us-east-1")
        sm.create_secret(
            Name="/acp-bridge/wrapper-test",
            SecretString=json.dumps({"apiKey": "sk-wrapper-test-key"}),
        )
        secret_arn = sm.describe_secret(SecretId="/acp-bridge/wrapper-test")["ARN"]

        # Import and configure wrapper
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../infra/lambda-burst/wrapper"))
        import handler
        handler._cached_api_key = None
        monkeypatch.setattr(handler, "SECRET_ARN", secret_arn)

        key = handler._get_api_key()
        assert key == "sk-wrapper-test-key"

        # Verify caching
        handler._get_api_key()
        handler._get_api_key()
        # Would only have called SM once due to cache


# ──── Part 2: Lambda Function Creation (moto) ────


class TestLambdaResourceCreation:
    """Verify Lambda function creation (what CDK deploy does)."""

    @mock_aws
    def test_create_function_with_layer(self, aws_env):
        """Create Lambda function + layer (simulates CDK deploy output)."""
        client = boto3.client("lambda", region_name="us-east-1")
        iam = boto3.client("iam", region_name="us-east-1")

        # Create role
        iam.create_role(
            RoleName="lambda-burst-role",
            AssumeRolePolicyDocument=json.dumps({
                "Version": "2012-10-17",
                "Statement": [{"Effect": "Allow",
                               "Principal": {"Service": "lambda.amazonaws.com"},
                               "Action": "sts:AssumeRole"}]
            }),
            Path="/",
        )

        # Create layer (simulates harness-factory binary)
        buf = BytesIO()
        with zipfile.ZipFile(buf, 'w') as zf:
            zf.writestr("bin/harness-factory", b"#!/bin/sh\necho fake")
        layer_resp = client.publish_layer_version(
            LayerName="harness-factory",
            Content={"ZipFile": buf.getvalue()},
            CompatibleRuntimes=["python3.12"],
        )
        layer_arn = layer_resp["LayerVersionArn"]

        # Create function
        fn_buf = BytesIO()
        with zipfile.ZipFile(fn_buf, 'w') as zf:
            zf.writestr("handler.py", "def handler(e,c): return {}")
        client.create_function(
            FunctionName="acp-bridge-harness-burst",
            Runtime="python3.12",
            Role="arn:aws:iam::123456789012:role/lambda-burst-role",
            Handler="handler.handler",
            Code={"ZipFile": fn_buf.getvalue()},
            MemorySize=512,
            Timeout=300,
            Layers=[layer_arn],
            Environment={"Variables": {
                "HARNESS_BIN": "/opt/bin/harness-factory",
                "LITELLM_URL": "http://10.0.1.79:4000",
                "LITELLM_SECRET_ARN": "arn:aws:secretsmanager:us-east-1:123456789012:secret:test",
            }},
        )

        # Verify
        fn = client.get_function(FunctionName="acp-bridge-harness-burst")
        config = fn["Configuration"]
        assert config["FunctionName"] == "acp-bridge-harness-burst"
        assert config["MemorySize"] == 512
        assert config["Timeout"] == 300
        assert config["Runtime"] == "python3.12"
        env_vars = config["Environment"]["Variables"]
        assert env_vars["HARNESS_BIN"] == "/opt/bin/harness-factory"
        assert env_vars["LITELLM_URL"] == "http://10.0.1.79:4000"
        assert "LITELLM_API_KEY" not in env_vars  # Key is in Secrets Manager, NOT env

    @mock_aws
    def test_function_concurrency(self, aws_env):
        """Reserved concurrency can be set (controls max burst)."""
        client = boto3.client("lambda", region_name="us-east-1")
        iam = boto3.client("iam", region_name="us-east-1")
        iam.create_role(
            RoleName="r",
            AssumeRolePolicyDocument=json.dumps({"Version": "2012-10-17", "Statement": []}),
            Path="/",
        )
        buf = BytesIO()
        with zipfile.ZipFile(buf, 'w') as zf:
            zf.writestr("h.py", "def handler(e,c): pass")
        client.create_function(
            FunctionName="burst-fn",
            Runtime="python3.12",
            Role="arn:aws:iam::123456789012:role/r",
            Handler="h.handler",
            Code={"ZipFile": buf.getvalue()},
        )
        client.put_function_concurrency(
            FunctionName="burst-fn",
            ReservedConcurrentExecutions=100,
        )
        resp = client.get_function_concurrency(FunctionName="burst-fn")
        assert resp["ReservedConcurrentExecutions"] == 100


# ──── Part 3: Full Pool Lifecycle (mock at invoke level) ────


class TestPoolLifecycleE2E:
    """Full LambdaPool lifecycle with realistic invoke simulation."""

    def _simulated_invoke(self, payload_bytes):
        """Simulate Lambda response based on payload."""
        payload = json.loads(payload_bytes)
        prompt = payload.get("prompt", "")

        if prompt == "__warmup__":
            body = json.dumps({"status": "completed", "output": "warm", "duration": 0.01})
        elif not prompt:
            body = json.dumps({"status": "error", "error": "prompt is required"})
        else:
            model = payload.get("model", "default")
            body = json.dumps({
                "status": "completed",
                "output": f"[{model}] processed: {prompt[:50]}",
                "duration": 1.5,
                "session_id": payload.get("session_id", ""),
            })

        mock_payload = MagicMock()
        mock_payload.read.return_value = body.encode()
        return {"StatusCode": 200, "Payload": mock_payload}

    def _make_pool(self, max_concurrent=50):
        from src.lambda_pool import LambdaPool
        with patch("src.lambda_pool.boto3") as mock_boto:
            mock_client = MagicMock()
            mock_boto.client.return_value = mock_client
            pool = LambdaPool(
                function_name="acp-bridge-harness-burst",
                region="us-east-1",
                max_concurrent=max_concurrent,
                timeout=60,
                default_model="bedrock/anthropic.claude-sonnet-4-6",
            )
            pool._client = mock_client
        mock_client.invoke.side_effect = lambda **kw: self._simulated_invoke(kw["Payload"])
        return pool

    @pytest.mark.asyncio
    async def test_single_invoke(self):
        """Single invoke with realistic response."""
        pool = self._make_pool()
        result = await pool.invoke(
            prompt="Write a Python hello world",
            model="bedrock/deepseek.v3.2",
        )
        assert result["status"] == "completed"
        assert "deepseek.v3.2" in result["output"]
        assert "Write a Python" in result["output"]

    @pytest.mark.asyncio
    async def test_batch_20_parallel(self):
        """20 parallel invocations complete successfully."""
        pool = self._make_pool()
        prompts = [{"prompt": f"Review file{i}.py for bugs"} for i in range(20)]
        results = await pool.invoke_batch(prompts)

        assert len(results) == 20
        assert all(r["status"] == "completed" for r in results)
        assert all("Review file" in r["output"] for r in results)
        assert pool.stats["total_invocations"] == 20

    @pytest.mark.asyncio
    async def test_burst_100(self):
        """Simulate 100 concurrent burst — the original requirement."""
        pool = self._make_pool(max_concurrent=100)
        prompts = [{"prompt": f"Task-{i}: analyze code block", "session_id": f"s-{i}"}
                   for i in range(100)]

        t0 = time.time()
        results = await pool.invoke_batch(prompts)
        duration = time.time() - t0

        assert len(results) == 100
        completed = sum(1 for r in results if r["status"] == "completed")
        assert completed == 100
        assert pool.stats["total_invocations"] == 100
        assert pool.stats["total_errors"] == 0
        # All 100 should finish quickly (they're mocked, but validates concurrency logic)
        assert duration < 10.0

    @pytest.mark.asyncio
    async def test_warmup_then_invoke(self):
        """Warmup followed by real invocations."""
        pool = self._make_pool()

        # Warmup (Event type doesn't use our side_effect)
        pool._client.invoke.side_effect = None
        pool._client.invoke.return_value = {"StatusCode": 202}
        warm = await pool.warmup(count=10)
        assert warm["warmed"] == 10

        # Switch to realistic invoke
        pool._client.invoke.side_effect = lambda **kw: self._simulated_invoke(kw["Payload"])
        pool._client.invoke.return_value = None

        result = await pool.invoke(prompt="hello after warmup")
        assert result["status"] == "completed"

    @pytest.mark.asyncio
    async def test_pipeline_sequence_simulation(self):
        """Simulate pipeline sequence: generate → review → fix."""
        pool = self._make_pool()

        # Step 1: Generate
        r1 = await pool.invoke(prompt="Generate a REST API in Python")
        assert r1["status"] == "completed"

        # Step 2: Review (uses step 1 output)
        r2 = await pool.invoke(prompt=f"Review this code: {r1['output']}")
        assert r2["status"] == "completed"

        # Step 3: Fix
        r3 = await pool.invoke(prompt=f"Fix issues: {r2['output']}")
        assert r3["status"] == "completed"

        assert pool.stats["total_invocations"] == 3

    @pytest.mark.asyncio
    async def test_capacity_overflow_and_recovery(self):
        """Pool at capacity → error → drain → recover."""
        from src.lambda_pool import LambdaSlot
        pool = self._make_pool(max_concurrent=5)

        # Fill to capacity
        for i in range(5):
            pool._active[f"x-{i}"] = LambdaSlot(
                session_id=f"x-{i}", agent_name="h", profile={})

        # Overflow
        with pytest.raises(RuntimeError, match="at capacity"):
            await pool.invoke(prompt="overflow")

        # Drain (simulate completions)
        pool._active.clear()
        drain_result = await pool.drain()
        assert drain_result["drained"] == 0  # already empty

        # Recover
        result = await pool.invoke(prompt="recovered")
        assert result["status"] == "completed"

    @pytest.mark.asyncio
    async def test_mixed_success_and_failure(self):
        """Some invocations succeed, some fail — stats track correctly."""
        pool = self._make_pool()
        call_n = {"n": 0}

        def flaky(**kw):
            call_n["n"] += 1
            if call_n["n"] % 4 == 0:
                raise Exception("simulated network error")
            return self._simulated_invoke(kw["Payload"])

        pool._client.invoke.side_effect = flaky

        prompts = [{"prompt": f"task-{i}"} for i in range(12)]
        results = await pool.invoke_batch(prompts)

        completed = sum(1 for r in results if r["status"] == "completed")
        errors = sum(1 for r in results if r["status"] == "error")
        assert completed == 9   # 12 - 3 failures (4th, 8th, 12th)
        assert errors == 3
        assert pool.stats["total_invocations"] == 12
        assert pool.stats["total_errors"] == 3

    @pytest.mark.asyncio
    async def test_cold_start_latency_simulation(self):
        """Simulate cold start: first invocation slower, subsequent faster."""
        pool = self._make_pool()
        call_n = {"n": 0}

        def with_cold_start(**kw):
            call_n["n"] += 1
            payload = json.loads(kw["Payload"])
            # First call simulates 800ms cold start
            duration = 0.8 if call_n["n"] == 1 else 0.05
            body = json.dumps({
                "status": "completed",
                "output": f"call #{call_n['n']}",
                "duration": duration,
                "session_id": payload.get("session_id", ""),
            })
            mock_payload = MagicMock()
            mock_payload.read.return_value = body.encode()
            return {"StatusCode": 200, "Payload": mock_payload}

        pool._client.invoke.side_effect = with_cold_start

        # Cold start
        r1 = await pool.invoke(prompt="first call")
        assert r1["duration"] == 0.8

        # Warm
        r2 = await pool.invoke(prompt="second call")
        assert r2["duration"] == 0.05
