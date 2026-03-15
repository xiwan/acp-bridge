"""Agent handlers — ACP mode + PTY fallback."""

import asyncio
import logging
import os
import re
import uuid
from collections.abc import AsyncGenerator

from acp_sdk.models import Message, MessagePart
from acp_sdk.server import Context, RunYield, RunYieldResume

from .acp_client import AcpConnection, AcpError, AcpProcessPool, PoolExhaustedError
from .sse import transform_notification

log = logging.getLogger("acp-bridge.agents")

ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]|\x1b\[\?25[hl]")


def strip_ansi(text: str) -> str:
    return ANSI_RE.sub("", text)


def make_acp_agent_handler(agent_name: str, pool: AcpProcessPool):
    """ACP protocol handler — structured events via stdio JSON-RPC."""

    async def handler(
        input: list[Message], context: Context
    ) -> AsyncGenerator[RunYield, RunYieldResume]:
        prompt = "".join(part.content for msg in input for part in msg.parts if part.content)
        session_id = str(context.session.id) if context.session else str(uuid.uuid4())

        log.info("acp_start: agent=%s session=%s len=%d", agent_name, session_id, len(prompt))

        try:
            conn = await pool.get_or_create(agent_name, session_id)
        except PoolExhaustedError as e:
            log.error("pool_exhausted: agent=%s: %s", agent_name, e)
            yield Message(parts=[MessagePart(content=f"[error] pool_exhausted: {e}", content_type="text/plain")])
            return
        except AcpError as e:
            log.error("agent_error: agent=%s: %s", agent_name, e)
            yield Message(parts=[MessagePart(content=f"[error] {e}", content_type="text/plain")])
            return

        if conn.session_reset:
            yield MessagePart(content="[status] 会话已过期，已自动创建新会话（之前的对话上下文已丢失）\n",
                              content_type="text/plain")
            conn.session_reset = False

        try:
            last_yield_time = asyncio.get_event_loop().time()
            heartbeat_interval = 15

            async for notification in conn.session_prompt(prompt):
                if "_prompt_result" in notification:
                    log.info("acp_done: agent=%s session=%s stop=%s",
                             agent_name, session_id,
                             notification["_prompt_result"].get("result", {}).get("stopReason", "?"))
                    continue

                event = transform_notification(notification)
                if event is None:
                    now = asyncio.get_event_loop().time()
                    if now - last_yield_time > heartbeat_interval:
                        yield MessagePart(content="", content_type="text/plain", name="heartbeat")
                        last_yield_time = now
                    continue

                last_yield_time = asyncio.get_event_loop().time()

                if event["type"] == "message.part":
                    yield MessagePart(content=event["content"], content_type="text/plain")
                elif event["type"] == "message.thinking":
                    yield MessagePart(content=event["content"], content_type="text/plain", name="thought")
                elif event["type"] in ("tool.start", "tool.done"):
                    yield MessagePart(
                        content=f"[{event['type']}] {event.get('title', '')} ({event.get('status', '')})\n",
                        content_type="text/plain")
                elif event["type"] == "status":
                    yield MessagePart(content=f"[status] {event['text']}\n", content_type="text/plain")

        except Exception as e:
            log.error("agent_crashed: agent=%s session=%s error=%s", agent_name, session_id, e)
            pool.remove(agent_name, session_id)
            yield Message(parts=[MessagePart(content=f"[error] agent_crashed: {e}", content_type="text/plain")])

    return handler


def make_pty_agent_handler(agent_cfg: dict, verbose: bool = False):
    """Legacy PTY handler — subprocess stdout line-by-line."""
    command = agent_cfg["command"]
    args = agent_cfg.get("args", [])

    async def handler(
        input: list[Message], context: Context
    ) -> AsyncGenerator[RunYield, RunYieldResume]:
        prompt = "".join(part.content for msg in input for part in msg.parts if part.content)
        session_id = str(context.session.id) if context.session else "default"

        log.info("pty_start: cmd=%s session=%s", command, session_id)

        env = os.environ.copy()
        env.update({"TERM": "dumb", "NO_COLOR": "1", "LANG": "en_US.UTF-8"})
        env.update(agent_cfg.get("env", {}))

        cmd = [command] + list(args) + [prompt]
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            stdin=asyncio.subprocess.DEVNULL,
            cwd=agent_cfg.get("working_dir", "/tmp"),
            env=env,
        )

        while True:
            line = await proc.stdout.readline()
            if not line:
                break
            text = strip_ansi(line.decode()).rstrip("\n")
            if text:
                yield MessagePart(content=text + "\n", content_type="text/plain")

        await proc.wait()
        log.info("pty_done: cmd=%s session=%s exit=%s", command, session_id, proc.returncode)

    return handler
