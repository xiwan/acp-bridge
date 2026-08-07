"""Unit tests for src/webhook.py — WebhookSender validates+pins at send time.

These prove the third upstream review gap directly: a callback_url is not
just checked once (e.g. at job submission) but revalidated on every actual
send — including retries and jobs recovered from the store, since both call
JobManager._webhook(), which always goes through WebhookSender.send().
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import httpx
import pytest

from src.webhook import WebhookSender


def _mock_transport(captured: list):
    async def handler(request: httpx.Request):
        captured.append(request)
        return httpx.Response(200, json={"id": "msg-1"})

    return httpx.MockTransport(handler)


@pytest.mark.asyncio
async def test_send_pins_connection_to_validated_ip_with_host_and_sni():
    """The actual outbound request must target the validated IP (pinned_url),
    not the original hostname — that's what closes the DNS-rebinding gap —
    while still carrying the right Host header and SNI extension."""
    captured: list[httpx.Request] = []
    sender = WebhookSender()
    sender._http = httpx.AsyncClient(transport=_mock_transport(captured))

    ok = await sender.send("https://example.com/hook", [{"message": "hi"}])

    assert ok is True
    assert len(captured) == 1
    req = captured[0]
    assert req.url.host != "example.com"  # pinned to the resolved literal IP
    assert req.headers["host"] == "example.com"
    assert req.extensions.get("sni_hostname") == "example.com"


@pytest.mark.asyncio
async def test_send_blocks_unsafe_url_without_connecting():
    """A private/loopback callback_url must be rejected at send time, not
    just at submit time — this is the actual security boundary."""
    captured: list[httpx.Request] = []
    sender = WebhookSender()
    sender._http = httpx.AsyncClient(transport=_mock_transport(captured))

    ok = await sender.send("http://127.0.0.1:9999/hook", [{"message": "hi"}])

    assert ok is False
    assert captured == []  # no connection was ever attempted


@pytest.mark.asyncio
async def test_send_allowed_targets_lets_listed_private_host_through():
    captured: list[httpx.Request] = []
    sender = WebhookSender(allowed_targets=frozenset({"127.0.0.1"}))
    sender._http = httpx.AsyncClient(transport=_mock_transport(captured))

    ok = await sender.send("http://127.0.0.1:9999/hook", [{"message": "hi"}])

    assert ok is True
    assert len(captured) == 1


@pytest.mark.asyncio
async def test_send_allowed_targets_never_bypass_metadata():
    captured: list[httpx.Request] = []
    sender = WebhookSender(allowed_targets=frozenset({"0.0.0.0/0"}))
    sender._http = httpx.AsyncClient(transport=_mock_transport(captured))

    ok = await sender.send("http://169.254.169.254/latest/meta-data/", [{"message": "hi"}])

    assert ok is False
    assert captured == []
