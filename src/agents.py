import asyncio
import logging
import os
import re
from collections.abc import AsyncGenerator
from pathlib import Path

import yaml
from acp_sdk.models import Message, MessagePart
from acp_sdk.server import Context, RunYield, RunYieldResume, Server

ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]|\x1b\[\?25[hl]")

log = logging.getLogger("acp-bridge.agent")


def load_config(path: str = "config.yaml") -> dict:
    with open(path) as f:
        return yaml.safe_load(f)


def strip_ansi(text: str) -> str:
    return ANSI_RE.sub("", text)


def extract_reply_kiro(raw: str) -> str:
    lines = strip_ansi(raw).strip().splitlines()
    content = []
    started = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("▸"):
            break
        if stripped.startswith("> "):
            content.append(stripped[2:])
            started = True
        elif started:
            cleaned = stripped.lstrip("│").strip()
            if cleaned:
                content.append(cleaned)
    return "\n".join(content).strip() or strip_ansi(raw).strip()


def extract_reply_raw(raw: str) -> str:
    return strip_ansi(raw).strip()


REPLY_EXTRACTORS = {
    "kiro": extract_reply_kiro,
    "raw": extract_reply_raw,
}

# raw mode supports streaming line-by-line; others need full output to parse
STREAMING_MODES = {"raw"}


async def has_session(command: str, cwd: str) -> bool:
    proc = await asyncio.create_subprocess_exec(
        command, "chat", "--list-sessions",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=cwd,
    )
    out, _ = await proc.communicate()
    return "SessionId" in out.decode()


async def _read_lines(stream) -> list[str]:
    lines = []
    while True:
        line = await stream.readline()
        if not line:
            break
        lines.append(line.decode())
    return lines


def make_agent_handler(agent_cfg: dict, verbose: bool = False):
    command = agent_cfg["command"]
    base_args = agent_cfg["args"]
    resume_flag = agent_cfg.get("resume_flag", "--resume")
    supports_resume = agent_cfg.get("supports_resume", True)
    session_base = Path(agent_cfg.get("session_base", "/tmp/acp-bridge-sessions"))
    output_mode = agent_cfg.get("output_mode", "raw")
    extract_reply = REPLY_EXTRACTORS.get(output_mode, extract_reply_raw)
    streamable = output_mode in STREAMING_MODES

    async def handler(
        input: list[Message], context: Context
    ) -> AsyncGenerator[RunYield, RunYieldResume]:
        prompt = "".join(
            part.content for msg in input for part in msg.parts
        )

        session_id = str(context.session.id) if context.session else "default"
        session_dir = session_base / session_id
        session_dir.mkdir(parents=True, exist_ok=True)

        log.info("📨 session=%s agent=%s prompt=%s", session_id, command, prompt[:80])
        log.debug("📂 session_dir=%s", session_dir)

        cmd = [command] + base_args.copy()
        resuming = False
        if supports_resume:
            resuming = await has_session(command, str(session_dir))
            if resuming:
                cmd.append(resume_flag)
        cmd.append(prompt)

        log.info("🔧 exec: %s (resume=%s, stream=%s)", " ".join(cmd[:4]) + " ...", resuming, streamable)
        log.debug("🔧 full cmd: %s", cmd)

        env = os.environ.copy()
        env.update({"TERM": "dumb", "NO_COLOR": "1", "LANG": "en_US.UTF-8"})

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            stdin=asyncio.subprocess.DEVNULL,
            cwd=str(session_dir),
            env=env,
        )

        if streamable:
            # Stream mode: yield each line as a MessagePart as it arrives
            total_len = 0
            while True:
                line = await proc.stdout.readline()
                if not line:
                    break
                text = strip_ansi(line.decode()).rstrip("\n")
                if text:
                    total_len += len(text)
                    yield MessagePart(content=text + "\n", content_type="text/plain")

            stderr_data = await proc.stderr.read()
            await proc.wait()

            # If nothing came from stdout, try stderr
            if total_len == 0 and stderr_data:
                log.debug("⚠️  stdout empty, falling back to stderr")
                text = strip_ansi(stderr_data.decode()).strip()
                if text:
                    yield MessagePart(content=text, content_type="text/plain")
                    total_len = len(text)

            log.info("✅ session=%s exit=%s streamed_len=%d", session_id, proc.returncode, total_len)
        else:
            # Buffered mode: collect all output, parse, then yield
            stdout_lines, stderr_lines = await asyncio.gather(
                _read_lines(proc.stdout),
                _read_lines(proc.stderr),
            )
            await proc.wait()

            raw = "".join(stdout_lines)
            stderr_text = "".join(stderr_lines)
            if not raw.strip() and stderr_text:
                log.debug("⚠️  stdout empty, falling back to stderr")
                raw = stderr_text
            reply = extract_reply(raw)

            log.info("✅ session=%s exit=%s stdout_len=%d stderr_len=%d reply_len=%d",
                     session_id, proc.returncode, len(raw), len(stderr_text), len(reply))
            if verbose:
                log.debug("📤 stdout:\n%s", strip_ansi(raw)[:2000])
                if stderr_text:
                    log.debug("⚠️  stderr:\n%s", stderr_text[:500])

            yield Message(parts=[MessagePart(content=reply, content_type="text/plain")])

    return handler
