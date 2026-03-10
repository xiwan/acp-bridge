"""Async job manager — submit, poll, webhook callback."""

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field

import httpx

from .acp_client import AcpError, AcpProcessPool, PoolExhaustedError
from .sse import transform_notification

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

    def to_dict(self) -> dict:
        d = {"job_id": self.job_id, "agent": self.agent, "session_id": self.session_id,
             "status": self.status, "created_at": self.created_at}
        if self.status in ("completed", "failed"):
            d["result"] = self.result
            d["error"] = self.error
            d["tools"] = self.tools
            d["duration"] = round(self.completed_at - self.created_at, 1)
        return d


class JobManager:
    def __init__(self, pool: AcpProcessPool, webhook_url: str = "", webhook_token: str = ""):
        self._pool = pool
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

    async def _run(self, job: Job):
        job.status = "running"
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
                    job.tools.append(event.get("title", ""))
        except (PoolExhaustedError, AcpError) as e:
            job.error = str(e)
            job.status = "failed"
        except Exception as e:
            job.error = str(e)
            job.status = "failed"
            self._pool.remove(job.agent, job.session_id)

        job.result = "".join(parts)
        job.completed_at = time.time()
        log.info("job_done: job=%s status=%s len=%d duration=%.1fs",
                 job.job_id, job.status, len(job.result), job.completed_at - job.created_at)

        if job.callback_url:
            await self._webhook(job)

    async def _webhook(self, job: Job):
        url = job.callback_url
        is_discord = "discord.com/api/webhooks" in url

        if is_discord:
            payload = self._format_discord(job)
        else:
            # OpenClaw /tools/invoke format
            payload = self._format_openclaw(job)

        headers = {"Content-Type": "application/json"}
        if self._webhook_token:
            headers["Authorization"] = f"Bearer {self._webhook_token}"
        # OpenClaw gateway needs account context from the job
        if not is_discord:
            account_id = job.callback_meta.get("account_id", "")
            if account_id:
                headers["x-openclaw-account-id"] = account_id
                headers["x-openclaw-message-channel"] = "discord"

        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(url, json=payload, headers=headers)
                log.info("webhook_sent: job=%s target=%s status=%d",
                         job.job_id, "discord" if is_discord else "openclaw", resp.status_code)
        except Exception as e:
            log.error("webhook_failed: job=%s error=%s", job.job_id, e)

    @staticmethod
    def _format_openclaw(job: Job) -> dict:
        """Format as OpenClaw /tools/invoke payload."""
        target = job.callback_meta.get("discord_target", "")
        if not target:
            # fallback: return raw JSON payload
            return {**job.to_dict(), **job.callback_meta}

        # Build markdown message
        lines = [f"**🤖 {job.agent}**", ""]
        if job.status == "failed":
            lines.append(f"❌ {job.error}")
        else:
            if job.tools:
                lines.append("🔧 **Tools**")
                for t in job.tools[:10]:
                    lines.append(f"✅ `{t}`")
                lines.append("")
            text = job.result[:3800]
            if len(job.result) > 3800:
                text += "\n\n_(truncated)_"
            lines.append(text)
        lines.extend(["", "---", f"_job: `{job.job_id[:8]}…` | {round(job.completed_at - job.created_at, 1)}s_"])

        return {
            "tool": "message",
            "action": "send",
            "args": {
                "channel": "discord",
                "target": target,
                "message": "\n".join(lines),
            },
        }

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

    def cleanup(self, max_age: float = 3600):
        cutoff = time.time() - max_age
        stale = [jid for jid, j in self._jobs.items() if j.completed_at > 0 and j.completed_at < cutoff]
        for jid in stale:
            del self._jobs[jid]
