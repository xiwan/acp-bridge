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

    async def send(self, url: str, payloads: list[dict], *,
                   secret: str = "", account_id: str = "",
                   channel: str = "", log_prefix: str = "webhook") -> bool:
        """Send payloads to url. Returns True if all succeeded.

        Payloads with thread_content=True are sent as thread-reply to the
        first message's ID (requires OpenClaw message tool support).
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

        try:
            client = await self._get_http()
            first_message_id: str = ""

            for idx, payload in enumerate(payloads):
                # Separate thread_content flag from the payload sent over the wire
                is_thread = payload.pop("thread_content", False)

                # If this is thread content and we have a parent message ID,
                # switch to thread-reply action
                if is_thread and first_message_id:
                    payload = dict(payload)
                    payload["action"] = "thread-reply"
                    payload["args"] = {**payload.get("args", {}), "message_id": first_message_id}

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

                # Capture message_id from first (summary) message for thread replies
                if idx == 0 and not first_message_id:
                    try:
                        resp_data = resp.json()
                        first_message_id = (
                            resp_data.get("message_id") or
                            resp_data.get("id") or
                            resp_data.get("data", {}).get("message_id", "")
                        )
                    except Exception:
                        pass

                if len(payloads) > 1:
                    await asyncio.sleep(0.5)
            return True
        except Exception as e:
            log.error("%s_failed: error=%s", log_prefix, e)
            return False
