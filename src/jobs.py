"""Async job manager — submit, poll, webhook callback."""

import asyncio
import logging
import os
import re
import time
import uuid
from dataclasses import dataclass, field

import httpx

from .acp_client import AcpError, AcpProcessPool, PoolExhaustedError
from .sse import transform_notification

ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]|\x1b\[\?25[hl]")

log = logging.getLogger("acp-bridge.jobs")


@dataclass
class Job:
    job_id: str
    agent: str
    session_id: str
    prompt: str
    status: str = "pending"
    result: str = ""
    error: str = ""
    tools: list[str] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    completed_at: float = 0
    callback_url: str = ""
    callback_meta: dict = field(default_factory=dict)
    webhook_sent: bool = False

    def to_dict(self) -> dict:
        d = {"job_id": self.job_id, "agent": self.agent, "session_id": self.session_id,
             "status": self.status, "created_at": self.created_at,
             "discord_target": self.callback_meta.get("discord_target", ""),
             "account_id": self.callback_meta.get("account_id", "")}
        if self.status == "running":
            d["elapsed"] = round(time.time() - self.created_at, 1)
        if self.status in ("completed", "failed"):
            d["result"] = self.result
            d["error"] = self.error
            d["tools"] = self.tools
            d["duration"] = round(self.completed_at - self.created_at, 1)
        return d


class JobManager:
    def __init__(self, pool: AcpProcessPool | None = None, pty_configs: dict | None = None,
                 webhook_url: str = "", webhook_token: str = ""):
        self._pool = pool
        self._pty_configs = pty_configs or {}
        self._jobs: dict[str, Job] = {}
        self._webhook_url = webhook_url
        self._webhook_token = webhook_token

    def submit(self, agent: str, session_id: str, prompt: str,
               callback_url: str = "", callback_meta: dict | None = None) -> Job:
        job = Job(
            job_id=str(uuid.uuid4()), agent=agent, session_id=session_id, prompt=prompt,
            callback_url=callback_url or self._webhook_url,
            callback_meta=callback_meta or {},
        )
        self._jobs[job.job_id] = job
        asyncio.create_task(self._run(job))
        log.info("job_submitted: job=%s agent=%s session=%s", job.job_id, agent, session_id)
        return job

    def get(self, job_id: str) -> Job | None:
        return self._jobs.get(job_id)

    def list_jobs(self, limit: int = 50) -> list[Job]:
        return sorted(self._jobs.values(), key=lambda j: j.created_at, reverse=True)[:limit]

    async def _run(self, job: Job):
        job.status = "running"
        if job.agent in self._pty_configs:
            await self._run_pty(job)
        else:
            await self._run_acp(job)
        job.completed_at = time.time()
        log.info("job_done: job=%s status=%s len=%d duration=%.1fs",
                 job.job_id, job.status, len(job.result), job.completed_at - job.created_at)
        if job.callback_url:
            await self._webhook(job)

    async def _run_acp(self, job: Job):
        parts = []
        try:
            conn = await self._pool.get_or_create(job.agent, job.session_id)
            async for notification in conn.session_prompt(job.prompt):
                if "_prompt_result" in notification:
                    if "error" in notification["_prompt_result"]:
                        job.error = str(notification["_prompt_result"]["error"])
                        job.status = "failed"
                    else:
                        job.status = "completed"
                    break
                event = transform_notification(notification)
                if not event:
                    continue
                if event["type"] == "message.part":
                    parts.append(event["content"])
                elif event["type"] == "tool.done":
                    title = event.get("title", "")
                    if title:
                        job.tools.append(title)
        except (PoolExhaustedError, AcpError) as e:
            job.error = str(e)
            job.status = "failed"
        except Exception as e:
            job.error = str(e)
            job.status = "failed"
            self._pool.remove(job.agent, job.session_id)
        job.result = "".join(parts)

    async def _run_pty(self, job: Job):
        cfg = self._pty_configs[job.agent]
        command = cfg["command"]
        args = cfg.get("args", [])
        idle_timeout = cfg.get("idle_timeout", 300)
        env = os.environ.copy()
        env.update({"TERM": "dumb", "NO_COLOR": "1", "LANG": "en_US.UTF-8"})
        env.update(cfg.get("env", {}))
        parts = []
        try:
            proc = await asyncio.create_subprocess_exec(
                command, *args, job.prompt,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                stdin=asyncio.subprocess.DEVNULL,
                cwd=cfg.get("working_dir", "/tmp"),
                env=env,
            )
            while True:
                try:
                    line = await asyncio.wait_for(proc.stdout.readline(), timeout=idle_timeout)
                except asyncio.TimeoutError:
                    log.warning("pty_timeout: job=%s cmd=%s idle=%ds", job.job_id, command, idle_timeout)
                    proc.kill()
                    await proc.wait()
                    job.error = f"agent timeout (idle {idle_timeout}s)"
                    job.status = "failed"
                    return
                if not line:
                    break
                text = ANSI_RE.sub("", line.decode()).rstrip("\n")
                if text:
                    parts.append(text + "\n")
            await proc.wait()
            job.status = "completed" if proc.returncode == 0 else "failed"
            if proc.returncode != 0:
                stderr = (await proc.stderr.read()).decode().strip()
                job.error = stderr or f"exit code {proc.returncode}"
        except Exception as e:
            job.error = str(e)
            job.status = "failed"
        job.result = "".join(parts)

    async def _webhook(self, job: Job):
        url = job.callback_url
        is_discord = "discord.com/api/webhooks" in url

        if is_discord:
            payloads = [self._format_discord(job)]
        else:
            payloads = self._format_openclaw(job)

        headers = {"Content-Type": "application/json"}
        if self._webhook_token:
            headers["Authorization"] = f"Bearer {self._webhook_token}"
        if not is_discord:
            account_id = job.callback_meta.get("account_id", "")
            if account_id:
                headers["x-openclaw-account-id"] = account_id
                headers["x-openclaw-message-channel"] = "discord"

        try:
            async with httpx.AsyncClient(timeout=10) as client:
                for payload in payloads:
                    resp = await client.post(url, json=payload, headers=headers)
                    log.info("webhook_sent: job=%s target=%s status=%d part=%d/%d",
                             job.job_id, "discord" if is_discord else "openclaw",
                             resp.status_code, payloads.index(payload) + 1, len(payloads))
                    if resp.status_code != 200:
                        break
                else:
                    job.webhook_sent = True
        except Exception as e:
            log.error("webhook_failed: job=%s error=%s", job.job_id, e)

    @staticmethod
    def _split_message(text: str, limit: int = 1900) -> list[str]:
        """Split text into chunks at line boundaries, each <= limit chars."""
        chunks, current = [], ""
        for line in text.split("\n"):
            # +1 for the newline character
            if current and len(current) + len(line) + 1 > limit:
                chunks.append(current)
                current = line
            else:
                current = f"{current}\n{line}" if current else line
        if current:
            chunks.append(current)
        return chunks

    @staticmethod
    def _format_openclaw(job: Job) -> list[dict]:
        """Format as OpenClaw /tools/invoke payloads, split for Discord 2000-char limit."""
        target = job.callback_meta.get("discord_target", "")
        if not target:
            return [{**job.to_dict(), **job.callback_meta}]

        duration = round(job.completed_at - job.created_at, 1)
        header = f"📨 **ACP Bridge** — {job.agent} `{job.job_id}`\n>"

        if job.status == "failed":
            body = f"> ❌ {job.error}"
        else:
            lines = []
            if job.tools:
                for t in job.tools[:10]:
                    lines.append(f"> 🔧 `{t}`")
                lines.append(">")
            for l in job.result.split("\n"):
                lines.append(f"> {l}")
            body = "\n".join(lines)

        footer = f">\n📨 **Done** — {duration}s"

        # Split body into chunks that fit within Discord limit
        # Reserve space for header (first chunk) and footer (last chunk)
        chunks = JobManager._split_message(body, limit=1900)
        total = len(chunks)
        payloads = []
        for i, chunk in enumerate(chunks):
            parts = []
            if i == 0:
                parts.append(header)
            if total > 1:
                parts.append(f"> **[{i+1}/{total}]**")
            parts.append(chunk)
            if i == total - 1:
                parts.append(footer)
            msg = "\n".join(parts)
            payloads.append({
                "tool": "message",
                "action": "send",
                "args": {"channel": "discord", "target": target, "message": msg},
            })
        return payloads

    @staticmethod
    def _format_discord(job: Job) -> dict:
        if job.status == "failed":
            desc = f"❌ {job.error}"
        else:
            # Discord embed description max 4096 chars
            text = job.result[:3900]
            if len(job.result) > 3900:
                text += "\n\n_(truncated)_"
            desc = text

        embed = {
            "title": f"🤖 {job.agent}",
            "description": desc,
            "color": 0x2ECC71 if job.status == "completed" else 0xE74C3C,
            "footer": {"text": f"job: {job.job_id[:8]}… | {round(job.completed_at - job.created_at, 1)}s"},
        }
        if job.tools:
            embed["fields"] = [{"name": "🔧 Tools", "value": "\n".join(f"✅ `{t}`" for t in job.tools[:10])}]

        return {"embeds": [embed]}

    def cleanup(self, max_age: float = 3600, stuck_timeout: float = 600):
        now = time.time()
        # Clean completed jobs older than max_age
        cutoff = now - max_age
        stale = [jid for jid, j in self._jobs.items() if j.completed_at > 0 and j.completed_at < cutoff]
        for jid in stale:
            del self._jobs[jid]
        # Mark stuck running jobs as failed
        for j in self._jobs.values():
            if j.status == "running" and now - j.created_at > stuck_timeout:
                log.warning("job_stuck: job=%s agent=%s duration=%.0fs, marking failed",
                            j.job_id, j.agent, now - j.created_at)
                j.status = "failed"
                j.error = f"timeout: job stuck for {int(now - j.created_at)}s"
                j.completed_at = now
                if j.callback_url:
                    asyncio.create_task(self._webhook(j))
        # Retry unsent webhooks for completed/failed jobs
        for j in self._jobs.values():
            if j.status in ("completed", "failed") and j.callback_url and not j.webhook_sent:
                log.info("webhook_retry: job=%s agent=%s", j.job_id, j.agent)
                asyncio.create_task(self._webhook(j))
