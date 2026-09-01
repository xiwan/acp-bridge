"""Tests for the Lambda wrapper handler (infra/lambda-burst/wrapper/handler.py).

These tests mock the subprocess (harness-factory) and Secrets Manager to validate
the handler logic in isolation.
"""

import json
import os
import sys
from unittest.mock import MagicMock, patch

import pytest

# Add wrapper to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../infra/lambda-burst/wrapper"))


@pytest.fixture(autouse=True)
def reset_cached_key():
    """Reset the cached API key between tests."""
    import handler

    handler._cached_api_key = None
    yield
    handler._cached_api_key = None


@pytest.fixture
def mock_secrets():
    """Mock Secrets Manager to return a test API key."""
    with patch("handler.boto3") as mock_boto:
        mock_client = MagicMock()
        mock_boto.client.return_value = mock_client
        mock_client.get_secret_value.return_value = {
            "SecretString": json.dumps({"apiKey": "sk-test-key-12345"})
        }
        yield mock_client


@pytest.fixture
def mock_env(monkeypatch):
    """Patch module-level constants that are read at import time."""
    import handler

    monkeypatch.setattr(handler, "SECRET_ARN", "arn:aws:secretsmanager:us-east-1:123:secret:test")
    monkeypatch.setattr(handler, "LITELLM_URL", "http://10.0.1.79:4000")
    monkeypatch.setattr(handler, "DEFAULT_MODEL", "bedrock/anthropic.claude-sonnet-4-6")
    monkeypatch.setattr(handler, "HARNESS_BIN", "/opt/bin/harness-factory")


class TestWarmup:
    """Tests for the __warmup__ path."""

    def test_warmup_returns_immediately(self, mock_env):
        import handler

        event = {"prompt": "__warmup__", "timeout": 10}
        result = handler.handler(event, None)
        assert result["status"] == "completed"
        assert result["output"] == "warm"
        assert result["duration"] >= 0

    def test_warmup_does_not_read_secrets(self, mock_env, mock_secrets):
        import handler

        event = {"prompt": "__warmup__"}
        handler.handler(event, None)
        mock_secrets.get_secret_value.assert_not_called()


class TestInputValidation:
    """Tests for input validation."""

    def test_empty_prompt_returns_error(self, mock_env):
        import handler

        event = {"prompt": ""}
        result = handler.handler(event, None)
        assert result["status"] == "error"
        assert "required" in result["error"]

    def test_missing_prompt_returns_error(self, mock_env):
        import handler

        event = {}
        result = handler.handler(event, None)
        assert result["status"] == "error"


class TestSecretRetrieval:
    """Tests for Secrets Manager integration."""

    def test_reads_json_secret(self, mock_env, mock_secrets):
        import handler

        key = handler._get_api_key()
        assert key == "sk-test-key-12345"
        mock_secrets.get_secret_value.assert_called_once()

    def test_reads_plain_string_secret(self, mock_env, mock_secrets):
        mock_secrets.get_secret_value.return_value = {"SecretString": "sk-plain-key-67890"}
        import handler

        key = handler._get_api_key()
        assert key == "sk-plain-key-67890"

    def test_caches_secret(self, mock_env, mock_secrets):
        import handler

        handler._get_api_key()
        handler._get_api_key()
        # Only called once due to caching
        assert mock_secrets.get_secret_value.call_count == 1

    def test_missing_secret_arn_raises(self, monkeypatch):
        import handler

        monkeypatch.setattr(handler, "SECRET_ARN", "")
        with pytest.raises(ValueError, match="LITELLM_SECRET_ARN"):
            handler._get_api_key()

    def test_secret_failure_returns_error(self, mock_env):
        import handler

        with patch("handler.boto3") as mock_boto:
            mock_client = MagicMock()
            mock_boto.client.return_value = mock_client
            mock_client.get_secret_value.side_effect = Exception("AccessDenied")

            event = {"prompt": "test task", "timeout": 5}
            result = handler.handler(event, None)
            assert result["status"] == "error"
            assert "secret" in result["error"]


class TestProfileInjection:
    """Tests for profile construction before spawning harness-factory."""

    def test_litellm_config_injected(self, mock_env, mock_secrets):
        import handler

        captured_profile = {}

        def fake_run(prompt, profile, cwd, timeout):
            captured_profile.update(profile)
            return "done"

        with patch.object(handler, "_run_acp_session", side_effect=fake_run):
            event = {
                "prompt": "test",
                "profile": {"tools": {"fs": {"permissions": ["read"]}}},
                "model": "bedrock/deepseek.v3.2",
            }
            handler.handler(event, None)

        assert captured_profile["agent"]["model"] == "bedrock/deepseek.v3.2"
        assert captured_profile["agent"]["litellm_url"] == "http://10.0.1.79:4000"
        assert captured_profile["agent"]["litellm_api_key"] == "sk-test-key-12345"
        assert captured_profile["tools"]["fs"]["permissions"] == ["read"]


class TestACPProtocol:
    """Tests for JSON-RPC communication helpers."""

    def test_rpc_format(self):
        import handler

        msg = handler._rpc(42, "session/new", {"cwd": "/tmp"})
        assert msg == {
            "jsonrpc": "2.0",
            "id": 42,
            "method": "session/new",
            "params": {"cwd": "/tmp"},
        }

    def test_fs_read_reply(self, tmp_path):
        import handler

        test_file = tmp_path / "test.txt"
        test_file.write_text("hello world")

        proc = MagicMock()
        sent_data = []
        proc.stdin.write = lambda d: sent_data.append(d)
        proc.stdin.flush = MagicMock()

        msg = {"id": 5, "method": "fs/read_text_file", "params": {"path": str(test_file)}}
        handler._reply_fs_read(proc, msg)

        reply = json.loads(sent_data[0].decode())
        assert reply["id"] == 5
        assert reply["result"]["content"] == "hello world"

    def test_fs_write_reply(self, tmp_path):
        import handler

        target = tmp_path / "out.txt"

        proc = MagicMock()
        sent_data = []
        proc.stdin.write = lambda d: sent_data.append(d)
        proc.stdin.flush = MagicMock()

        msg = {
            "id": 7,
            "method": "fs/write_text_file",
            "params": {"path": str(target), "content": "written content"},
        }
        handler._reply_fs_write(proc, msg)

        assert target.read_text() == "written content"
        reply = json.loads(sent_data[0].decode())
        assert reply["id"] == 7
        assert reply["result"] == {}

    def test_permission_auto_allow(self):
        import handler

        proc = MagicMock()
        sent_data = []
        proc.stdin.write = lambda d: sent_data.append(d)
        proc.stdin.flush = MagicMock()

        msg = {
            "id": 9,
            "method": "session/request_permission",
            "params": {"toolCall": {"title": "run bash"}},
        }
        handler._reply_allow(proc, msg)

        reply = json.loads(sent_data[0].decode())
        assert reply["id"] == 9
        assert reply["result"]["outcome"]["outcome"] == "selected"
        assert reply["result"]["outcome"]["optionId"] == "proceed_always"


class TestCleanup:
    """Tests for workspace cleanup."""

    def test_workspace_cleaned_after_invoke(self, mock_env, mock_secrets):
        import handler

        created_dir = None

        def fake_run(prompt, profile, cwd, timeout):
            nonlocal created_dir
            created_dir = cwd
            os.makedirs(cwd, exist_ok=True)
            # Write a file to verify cleanup
            with open(os.path.join(cwd, "artifact.txt"), "w") as f:
                f.write("test")
            return "done"

        with patch.object(handler, "_run_acp_session", side_effect=fake_run):
            event = {"prompt": "test", "timeout": 5}
            result = handler.handler(event, None)

        assert result["status"] == "completed"
        assert created_dir is not None
        assert not os.path.exists(created_dir)  # cleaned up
