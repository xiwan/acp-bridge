"""OpenClaw tools proxy endpoints."""

import logging

import httpx
from fastapi.responses import JSONResponse
from pydantic import BaseModel

log = logging.getLogger("acp-bridge.tools")


class ToolInvokeRequest(BaseModel):
    tool: str
    action: str = ""
    args: dict = {}
    channel: str = ""
    account_id: str = ""


def register(app, openclaw_url: str, openclaw_token: str, default_account_id: str):
    openclaw_base = openclaw_url.replace("/tools/invoke", "") if openclaw_url else ""
    _http: list = []  # mutable container for client reuse

    async def _get_http() -> httpx.AsyncClient:
        if not _http or _http[0].is_closed:
            _http.clear()
            _http.append(httpx.AsyncClient(timeout=30))
        return _http[0]

    @app.post("/tools/invoke")
    async def tools_invoke(req: ToolInvokeRequest):
        if not openclaw_url:
            return JSONResponse({"error": "webhook.url not configured"}, status_code=503)
        headers = {"Content-Type": "application/json"}
        if openclaw_token:
            headers["Authorization"] = f"Bearer {openclaw_token}"
        acct = req.account_id or default_account_id
        if acct:
            headers["x-openclaw-account-id"] = acct
        if req.channel:
            headers["x-openclaw-message-channel"] = req.channel
        elif req.args.get("channel"):
            headers["x-openclaw-message-channel"] = req.args["channel"]
        args = req.args
        if acct and "accountId" not in args:
            args = {**args, "accountId": acct}
        payload: dict = {"tool": req.tool, "args": args}
        if req.action:
            payload["action"] = req.action
        try:
            client = await _get_http()
            resp = await client.post(openclaw_url, json=payload, headers=headers)
            return JSONResponse(resp.json(), status_code=resp.status_code)
        except Exception as e:
            log.error("tools_invoke_failed: tool=%s error=%s", req.tool, e)
            return JSONResponse({"error": str(e)}, status_code=502)

    @app.get("/tools")
    async def list_tools():
        tools = [
            {"name": "message", "description": "Send messages across Discord/Telegram/Slack/WhatsApp/Signal/iMessage/MS Teams",
             "actions": ["send", "react", "edit", "delete", "pin", "search", "poll", "thread-create", "thread-reply"]},
            {"name": "tts", "description": "Convert text to speech audio", "actions": []},
            {"name": "web_search", "description": "Search the web", "actions": []},
            {"name": "web_fetch", "description": "Fetch and extract content from a URL", "actions": []},
            {"name": "nodes", "description": "Control paired devices (notify, run commands, camera, screen)",
             "actions": ["status", "notify", "run", "camera_snap", "camera_clip", "screen_record", "location_get"]},
            {"name": "cron", "description": "Manage scheduled jobs",
             "actions": ["status", "list", "add", "update", "remove", "run"]},
            {"name": "gateway", "description": "Gateway config and restart",
             "actions": ["restart", "config.get", "config.apply", "config.patch"]},
            {"name": "image", "description": "Analyze an image with AI", "actions": []},
            {"name": "browser", "description": "Control browser (open, screenshot, navigate)",
             "actions": ["status", "open", "screenshot", "snapshot", "navigate"]},
        ]
        return {"tools": tools, "openclaw_url": openclaw_base or "(not configured)"}
