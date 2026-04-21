"""Shared webhook sender — HTTP POST with HMAC signing, chunking, and auth."""

import asyncio
import hashlib
import hmac as _hmac
import json as _json
import logging

import httpx

log = logging.getLogger("acp-bridge.webhook")

_CHUNK_SIZE = 1800  # safe for Discord 2000-char limit after JSON overhead


def chunk_text(text: str, size: int = _CHUNK_SIZE) -> list[str]:
    """Split text into chunks of at most `size` characters."""
    if not text:
        return [""]
    return [text[i:i + size] for i in range(0, len(text), size)]


class WebhookSender:
    """Sends JSON payloads to a webhook URL with optional Bearer token or HMAC signing."""

    def __init__(self, default_url: str = "", default_token: str = "",
                 default_format: str = "openclaw", default_secret: str = ""):
        self._url = default_url
        self._token = default_token
        self._format = default_format
        self._secret = default_secret
        self._http: httpx.AsyncClient | None = None

    @property
    def default_url(self) -> str:
        return self._url

    @property
    def default_format(self) -> str:
        return self._format

    async def _get_http(self) -> httpx.AsyncClient:
        if self._http is None or self._http.is_closed:
            self._http = httpx.AsyncClient(timeout=10)
        return self._http

    async def _post(self, client: httpx.AsyncClient, url: str,
                    payload: dict, headers: dict, secret: str) -> httpx.Response:
        """Post a single payload, handling HMAC signing if needed."""
        req_headers = dict(headers)
        if secret:
            body_bytes = _json.dumps(payload, ensure_ascii=False).encode()
            sig = _hmac.new(secret.encode(), body_bytes, hashlib.sha256).hexdigest()
            req_headers["X-Webhook-Signature"] = sig
            return await client.post(url, content=body_bytes, headers=req_headers)
        return await client.post(url, json=payload, headers=req_headers)

    @staticmethod
    def _extract_id(resp: httpx.Response) -> str:
        """Extract message/thread id from OpenClaw response."""
        try:
            d = resp.json()
            return (d.get("id") or d.get("message_id") or
                    d.get("data", {}).get("id", "") or
                    d.get("data", {}).get("message_id", ""))
        except Exception:
            return ""

    async def send(self, url: str, payloads: list[dict], *,
                   secret: str = "", account_id: str = "",
                   channel: str = "", log_prefix: str = "webhook") -> bool:
        """Send payloads to url. Returns True if all succeeded.

        For Discord: thread_content payloads are folded into a real thread
        (thread-create on the first message, then send into the thread).
        For other channels: thread_content payloads are sent normally.
        """
        if not url or not payloads:
            return False

        headers = {"Content-Type": "application/json"}
        if secret:
            pass  # HMAC signed per-payload below
        elif self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        if account_id:
            headers["x-openclaw-account-id"] = account_id
            headers["x-openclaw-message-channel"] = channel

        is_discord = channel == "discord"

        try:
            client = await self._get_http()
            first_message_id: str = ""
            thread_id: str = ""

            for idx, payload in enumerate(payloads):
                is_thread = payload.pop("thread_content", False)

                # Discord thread folding: create thread then send into it
                if is_thread and first_message_id and is_discord:
                    # Step 1: create thread on the summary message
                    if not thread_id:
                        thread_payload = dict(payload)
                        thread_payload["action"] = "thread-create"
                        thread_content = payload.get("args", {}).get("message", "Thread")
                        thread_name = thread_content[:97] + "..." if len(thread_content) > 100 else thread_content
                        thread_payload["args"] = {"message_id": first_message_id, "name": thread_name}
                        resp = await self._post(client, url, thread_payload, headers, secret)
                        log.info("%s: thread-create status=%d", log_prefix, resp.status_code)
                        if resp.status_code < 300:
                            thread_id = self._extract_id(resp)

                    # Step 2: send full content into the thread
                    if thread_id:
                        payload = dict(payload)
                        payload["action"] = "send"
                        payload["args"] = {**payload.get("args", {}), "target": thread_id}
                    # else: fallback — send as normal message (no thread_id)

                req_headers = dict(headers)
                if secret:
                    body_bytes = _json.dumps(payload, ensure_ascii=False).encode()
                    sig = _hmac.new(secret.encode(), body_bytes, hashlib.sha256).hexdigest()
                    req_headers["X-Webhook-Signature"] = sig
                    resp = await client.post(url, content=body_bytes, headers=req_headers)
                else:
                    resp = await client.post(url, json=payload, headers=req_headers)
                log.info("%s: status=%d part=%d/%d thread=%s",
                         log_prefix, resp.status_code, idx + 1, len(payloads), is_thread)
                if resp.status_code >= 300:
                    log.warning("%s_rejected: status=%d body=%s",
                                log_prefix, resp.status_code, resp.text[:500])
                    return False

                # Capture message_id from first (summary) message
                if idx == 0 and not first_message_id:
                    first_message_id = self._extract_id(resp)

                if len(payloads) > 1:
                    await asyncio.sleep(0.5)
            return True
        except Exception as e:
            log.error("%s_failed: error=%s", log_prefix, e)
            return False
