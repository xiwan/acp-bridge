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
from .formatters import get_formatter
from .sse import transform_notification
from .store import JobStore

ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]|\x1b\[\?25[hl]")

log = logging.getLogger("acp-bridge.jobs")


@dataclass
class Job:
    job_id: str
    agent: str
    session_id: str
    prompt: str
    cwd: str = ""
    status: str = "pending"
    result: str = ""
    error: str = ""
    tools: list[str] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    completed_at: float = 0
    callback_url: str = ""
    callback_meta: dict = field(default_factory=dict)
    webhook_sent: bool = False
    retries: int = 0

    def to_dict(self) -> dict:
        d = {"job_id": self.job_id, "agent": self.agent, "session_id": self.session_id,
             "status": self.status, "created_at": self.created_at,
             "target": self.callback_meta.get("target", self.callback_meta.get("discord_target", "")),
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
                 webhook_url: str = "", webhook_token: str = "", base_url: str = "",
                 db_path: str = "data/jobs.db"):
        self._pool = pool
        self._pty_configs = pty_configs or {}
        self._jobs: dict[str, Job] = {}
        self._webhook_url = webhook_url
        self._webhook_token = webhook_token
        self._base_url = base_url
        self._http: httpx.AsyncClient | None = None
        self._store = JobStore(db_path)
        self._recover_jobs()

    async def _get_http(self) -> httpx.AsyncClient:
        if self._http is None or self._http.is_closed:
            self._http = httpx.AsyncClient(timeout=10)
        return self._http

    def _recover_jobs(self):
        """On startup: queue incomplete jobs for background retry, reload unsent webhooks."""
        # Load unsent webhooks for retry
        for d in self._store.load_unsent_webhooks():
            job = self._dict_to_job(d)
            self._jobs[job.job_id] = job
            log.info("recovered_webhook: job=%s agent=%s", job.job_id, job.agent)
        # Queue incomplete jobs — will be retried in background task
        self._pending_recovery = [self._dict_to_job(d) for d in self._store.load_incomplete()]
        if self._pending_recovery:
            log.info("recovery_queued: %d incomplete jobs for background retry", len(self._pending_recovery))

    async def run_recovery(self, max_retries: int = 3):
        """Background task: retry incomplete jobs up to max_retries, then fail."""
        jobs = self._pending_recovery
        self._pending_recovery = []
        for job in jobs:
            job.retries += 1
            if job.retries > max_retries:
                job.status = "failed"
                job.error = f"interrupted: failed after {max_retries} retries across restarts"
                job.completed_at = time.time()
                self._jobs[job.job_id] = job
                self._store.save(job)
                log.warning("recovery_failed: job=%s agent=%s retries=%d", job.job_id, job.agent, job.retries)
                if job.callback_url:
                    await self._webhook(job)
                continue
            # Reset for re-execution
            log.info("recovery_retry: job=%s agent=%s attempt=%d/%d",
                     job.job_id, job.agent, job.retries, max_retries)
            job.status = "pending"
            job.result = ""
            job.error = ""
            job.tools = []
            job.completed_at = 0
            self._jobs[job.job_id] = job
            self._store.save(job)
            await self._run(job)

    @staticmethod
    def _dict_to_job(d: dict) -> Job:
        return Job(
            job_id=d["job_id"], agent=d["agent"], session_id=d["session_id"],
            prompt=d["prompt"], cwd=d.get("cwd", ""), status=d["status"],
            result=d.get("result", ""), error=d.get("error", ""),
            tools=d.get("tools", []), created_at=d["created_at"],
            completed_at=d.get("completed_at", 0),
            callback_url=d.get("callback_url", ""),
            callback_meta=d.get("callback_meta", {}),
            webhook_sent=d.get("webhook_sent", False),
            retries=d.get("retries", 0),
        )

    def submit(self, agent: str, session_id: str, prompt: str,
               callback_url: str = "", callback_meta: dict | None = None,
               cwd: str = "") -> Job:
        job = Job(
            job_id=str(uuid.uuid4()), agent=agent, session_id=session_id, prompt=prompt,
            cwd=cwd,
            callback_url=callback_url or self._webhook_url,
            callback_meta=callback_meta or {},
        )
        self._jobs[job.job_id] = job
        self._store.save(job)
        asyncio.create_task(self._run(job))
        log.info("job_submitted: job=%s agent=%s session=%s", job.job_id, agent, session_id)
        return job

    def get(self, job_id: str) -> Job | None:
        job = self._jobs.get(job_id)
        if job:
            return job
        # Fallback to DB for historical jobs
        rows = self._store._db.execute(
            "SELECT * FROM jobs WHERE job_id = ?", (job_id,)
        ).fetchall()
        if rows:
            return self._dict_to_job(self._store._row_to_dict(rows[0]))
        return None

    def list_jobs(self, limit: int = 50) -> list[Job]:
        # Merge in-memory jobs with DB historical jobs
        seen = set(self._jobs.keys())
        jobs = list(self._jobs.values())
        for d in self._store.load_recent(limit):
            if d["job_id"] not in seen:
                jobs.append(self._dict_to_job(d))
        return sorted(jobs, key=lambda j: j.created_at, reverse=True)[:limit]

    async def _run(self, job: Job):
        job.status = "running"
        self._store.save(job)
        if job.agent in self._pty_configs:
            await self._run_pty(job)
        else:
            await self._run_acp(job)
        job.completed_at = time.time()
        self._store.save(job)
        log.info("job_done: job=%s status=%s len=%d duration=%.1fs",
                 job.job_id, job.status, len(job.result), job.completed_at - job.created_at)
        if job.callback_url:
            await self._webhook(job)

    async def _run_acp(self, job: Job):
        parts = []
        try:
            conn = await self._pool.get_or_create(job.agent, job.session_id, cwd=job.cwd)
            prompt = job.prompt + "\n\n[IMPORTANT: Output all results as text in your reply. Do not write to files — this is an async job and the user can only see your text output.]"
            async for notification in conn.session_prompt(prompt):
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
                cwd=job.cwd or cfg.get("working_dir", "/tmp"),
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
        is_discord_webhook = "discord.com/api/webhooks" in url
        target = job.callback_meta.get("target", job.callback_meta.get("discord_target", ""))
        channel = job.callback_meta.get("channel", "discord")

        if is_discord_webhook:
            payloads = [self._format_discord_embed(job)]
        elif target:
            formatter = get_formatter(channel)
            payloads = formatter.format(job, target, base_url=self._base_url)
        else:
            payloads = [{**job.to_dict(), **job.callback_meta}]

        headers = {"Content-Type": "application/json"}
        if self._webhook_token:
            headers["Authorization"] = f"Bearer {self._webhook_token}"
        if not is_discord_webhook:
            account_id = job.callback_meta.get("account_id", "")
            if account_id:
                headers["x-openclaw-account-id"] = account_id
                headers["x-openclaw-message-channel"] = channel

        try:
            client = await self._get_http()
            for payload in payloads:
                resp = await client.post(url, json=payload, headers=headers)
                log.info("webhook_sent: job=%s channel=%s status=%d part=%d/%d",
                         job.job_id, channel,
                         resp.status_code, payloads.index(payload) + 1, len(payloads))
                if resp.status_code != 200:
                    break
                if len(payloads) > 1:
                    await asyncio.sleep(0.5)
            else:
                job.webhook_sent = True
                self._store.save(job)
        except Exception as e:
            log.error("webhook_failed: job=%s error=%s", job.job_id, e)

    @staticmethod
    def _format_discord_embed(job: Job) -> dict:
        """Legacy: direct Discord webhook with embed."""
        if job.status == "failed":
            desc = f"❌ {job.error}"
        else:
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
                self._store.save(j)
                if j.callback_url:
                    asyncio.create_task(self._webhook(j))
        # Retry unsent webhooks for completed/failed jobs
        for j in self._jobs.values():
            if j.status in ("completed", "failed") and j.callback_url and not j.webhook_sent:
                log.info("webhook_retry: job=%s agent=%s", j.job_id, j.agent)
                asyncio.create_task(self._webhook(j))
        # Purge old rows from sqlite
        deleted = self._store.delete_old(max_age)
        if deleted:
            log.info("store_cleanup: deleted %d old jobs", deleted)
