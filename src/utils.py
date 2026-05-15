"""Shared utilities — ANSI stripping, PTY subprocess execution."""

import asyncio
import os
import re
import time
from dataclasses import dataclass

ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]|\x1b\[\?25[hl]")


def strip_ansi(text: str) -> str:
    return ANSI_RE.sub("", text)


@dataclass
class PtyResult:
    status: str  # "completed" | "failed"
    output: str
    error: str
    duration: float = 0.0


async def run_pty_subprocess(
    command: str,
    args: list[str],
    prompt: str,
    cwd: str = "/tmp",
    env_overrides: dict | None = None,
    idle_timeout: float = 300,
    max_duration: float = 600,
) -> PtyResult:
    """Run a CLI agent as a one-shot subprocess, streaming stdout line-by-line.

    Returns PtyResult with status, output, error, and duration.
    """
    env = os.environ.copy()
    env.update({"TERM": "dumb", "NO_COLOR": "1", "LANG": "en_US.UTF-8"})
    if env_overrides:
        env.update(env_overrides)

    os.makedirs(cwd, exist_ok=True)
    t0 = time.time()
    parts: list[str] = []

    proc = await asyncio.create_subprocess_exec(
        command, *args, prompt,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        stdin=asyncio.subprocess.DEVNULL,
        cwd=cwd,
        env=env,
    )

    try:
        while True:
            if time.time() - t0 > max_duration:
                proc.kill()
                await proc.wait()
                return PtyResult(
                    status="failed", output="".join(parts),
                    error=f"agent exceeded max_duration ({max_duration}s)",
                    duration=time.time() - t0,
                )
            try:
                line = await asyncio.wait_for(proc.stdout.readline(), timeout=idle_timeout)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
                return PtyResult(
                    status="failed", output="".join(parts),
                    error=f"agent timeout (idle {idle_timeout}s)",
                    duration=time.time() - t0,
                )
            except (asyncio.LimitOverrunError, ValueError):
                continue
            if not line:
                break
            text = strip_ansi(line.decode()).rstrip("\n")
            if text:
                parts.append(text + "\n")

        await proc.wait()
        status = "completed" if proc.returncode == 0 else "failed"
        error = ""
        if proc.returncode != 0:
            stderr = (await proc.stderr.read()).decode().strip()
            error = stderr or f"exit code {proc.returncode}"
    except Exception as e:
        proc.kill()
        await proc.wait()
        return PtyResult(
            status="failed", output="".join(parts),
            error=str(e), duration=time.time() - t0,
        )

    return PtyResult(status=status, output="".join(parts), error=error, duration=time.time() - t0)
