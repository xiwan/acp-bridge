"""Multi-agent pipeline — sequence, parallel, race execution."""

import asyncio
import logging
import os
import random
import re
import time
import uuid
from dataclasses import dataclass, field

import httpx

from .acp_client import AcpError, AcpProcessPool, PoolExhaustedError
from .sse import transform_notification
from .store import PipelineStore

log = logging.getLogger("acp-bridge.pipeline")

ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]|\x1b\[\?25[hl]")


@dataclass
class PipelineStep:
    agent: str
    prompt_template: str
    output_as: str = ""
    timeout: float = 600
    status: str = "pending"
    result: str = ""
    error: str = ""
    started_at: float = 0
    completed_at: float = 0

    def to_dict(self) -> dict:
        d = {"agent": self.agent, "status": self.status}
        if self.result:
            d["result"] = self.result
        if self.error:
            d["error"] = self.error
        if self.completed_at and self.started_at:
            d["duration"] = round(self.completed_at - self.started_at, 1)
        return d


@dataclass
class Pipeline:
    pipeline_id: str
    mode: str
    steps: list[PipelineStep]
    status: str = "pending"
    context: dict = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)
    completed_at: float = 0
    error: str = ""
    webhook_meta: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        d = {
            "pipeline_id": self.pipeline_id,
            "mode": self.mode,
            "status": self.status,
            "steps": [s.to_dict() for s in self.steps],
            "created_at": self.created_at,
        }
        if self.completed_at:
            d["duration"] = round(self.completed_at - self.created_at, 1)
        if self.error:
            d["error"] = self.error
        return d


class PipelineManager:
    def __init__(self, pool: AcpProcessPool, agents_cfg: dict,
                 webhook_url: str = "", webhook_token: str = "",
                 db_path: str = "data/jobs.db"):
        self._pool = pool
        self._agents_cfg = agents_cfg
        self._pipelines: dict[str, Pipeline] = {}
        self._webhook_url = webhook_url
        self._webhook_token = webhook_token
        self._http: httpx.AsyncClient | None = None
        self._store = PipelineStore(db_path)

    def submit(self, mode: str, steps: list[dict], context: dict | None = None,
               webhook_meta: dict | None = None) -> Pipeline:
        pl = Pipeline(
            pipeline_id=str(uuid.uuid4()),
            mode=mode,
            steps=[PipelineStep(
                agent=s["agent"],
                prompt_template=s["prompt"],
                output_as=s.get("output_as", ""),
                timeout=s.get("timeout", 600),
            ) for s in steps],
            context=context or {},
            webhook_meta=webhook_meta or {},
        )
        self._pipelines[pl.pipeline_id] = pl
        self._store.save(pl)
        asyncio.create_task(self._run(pl))
        log.info("pipeline_submitted: id=%s mode=%s steps=%d", pl.pipeline_id, mode, len(pl.steps))
        return pl

    def get(self, pipeline_id: str) -> Pipeline | None:
        pl = self._pipelines.get(pipeline_id)
        if pl:
            return pl
        d = self._store.get(pipeline_id)
        if d:
            return self._dict_to_pipeline(d)
        return None

    def list_all(self, limit: int = 50) -> list[Pipeline]:
        seen = set(self._pipelines.keys())
        pls = list(self._pipelines.values())
        for d in self._store.load_recent(limit):
            if d["pipeline_id"] not in seen:
                pls.append(self._dict_to_pipeline(d))
        return sorted(pls, key=lambda p: p.created_at, reverse=True)[:limit]

    @staticmethod
    def _dict_to_pipeline(d: dict) -> Pipeline:
        pl = Pipeline(
            pipeline_id=d["pipeline_id"], mode=d["mode"],
            steps=[PipelineStep(
                agent=s["agent"], prompt_template=s.get("prompt_template", ""),
                output_as=s.get("output_as", ""), status=s.get("status", ""),
                result=s.get("result", ""), error=s.get("error", ""),
                started_at=s.get("started_at", 0), completed_at=s.get("completed_at", 0),
            ) for s in d.get("steps", [])],
            status=d["status"], context=d.get("context", {}),
            created_at=d["created_at"], completed_at=d.get("completed_at", 0),
            error=d.get("error", ""), webhook_meta=d.get("webhook_meta", {}),
        )
        return pl

    async def _run(self, pl: Pipeline):
        pl.status = "running"
        try:
            if pl.mode == "sequence":
                await self._run_sequence(pl)
            elif pl.mode == "parallel":
                await self._run_parallel(pl)
            elif pl.mode == "race":
                await self._run_race(pl)
            elif pl.mode == "random":
                await self._run_random(pl)
            else:
                pl.error = f"unknown mode: {pl.mode}"
                pl.status = "failed"
        except Exception as e:
            pl.error = str(e)
            pl.status = "failed"
            log.error("pipeline_error: id=%s error=%s", pl.pipeline_id, e)
        pl.completed_at = time.time()
        if pl.status == "running":
            pl.status = "completed" if not pl.error else "failed"
        self._store.save(pl)
        log.info("pipeline_done: id=%s status=%s duration=%.1fs",
                 pl.pipeline_id, pl.status, pl.completed_at - pl.created_at)
        await self._webhook(pl)

    async def _send_webhook(self, pl: Pipeline, message: str):
        """Send a single message payload via webhook."""
        url = self._webhook_url
        if not url:
            return
        target = pl.webhook_meta.get("target", "")
        channel = pl.webhook_meta.get("channel", "discord")
        account_id = pl.webhook_meta.get("account_id", "")
        payload = {"tool": "message", "action": "send",
                   "args": {"channel": channel, "target": target, "message": message}}
        headers = {"Content-Type": "application/json"}
        if self._webhook_token:
            headers["Authorization"] = f"Bearer {self._webhook_token}"
        if account_id:
            headers["x-openclaw-account-id"] = account_id
            headers["x-openclaw-message-channel"] = channel
        try:
            if not self._http or self._http.is_closed:
                self._http = httpx.AsyncClient(timeout=10)
            resp = await self._http.post(url, json=payload, headers=headers)
            log.info("pipeline_webhook: id=%s status=%d", pl.pipeline_id, resp.status_code)
            if resp.status_code == 401:
                log.warning("pipeline_webhook_unauthorized: id=%s — OPENCLAW_TOKEN may be missing or wrong",
                            pl.pipeline_id)
            elif resp.status_code != 200:
                log.warning("pipeline_webhook_rejected: id=%s body=%s",
                            pl.pipeline_id, resp.text[:300])
        except Exception as e:
            log.error("pipeline_webhook_failed: id=%s error=%s", pl.pipeline_id, e)

    async def _webhook_step(self, pl: Pipeline, step: PipelineStep):
        """Push a single step result immediately after it completes."""
        if not self._webhook_url or not pl.webhook_meta.get("target"):
            return
        dur = round(step.completed_at - step.started_at, 1)
        icon = "✅" if step.status == "completed" else "❌"
        lines = [f"🔗 **Pipeline** `{pl.pipeline_id[:8]}` — **{step.agent}** {icon}"]
        if step.status == "failed":
            lines.append(f"> ❌ {step.error}")
        else:
            preview = step.result[:300] + "..." if len(step.result) > 300 else step.result
            for ln in preview.splitlines():
                lines.append(f"> {ln}")
        lines.append(f"> ⏱️ {dur}s")
        await self._send_webhook(pl, "\n".join(lines))

    async def _webhook(self, pl: Pipeline):
        """Final summary push — overall status + total duration."""
        if not self._webhook_url or not pl.webhook_meta.get("target"):
            return
        dur = round(pl.completed_at - pl.created_at, 1)
        if pl.status == "failed":
            msg = f"🔗 **Pipeline** `{pl.pipeline_id[:8]}` ❌ {pl.error} | 耗时 {dur}s"
        else:
            msg = f"🔗 **Pipeline** `{pl.pipeline_id[:8]}` ✅ 全部完成，耗时 {dur}s"
        await self._send_webhook(pl, msg)

    async def _run_sequence(self, pl: Pipeline):
        for step in pl.steps:
            prompt = self._render(step.prompt_template, pl.context)
            await self._exec_step(pl, step, prompt)
            await self._webhook_step(pl, step)
            if step.status == "failed":
                pl.status = "failed"
                pl.error = f"step {step.agent} failed: {step.error}"
                return
            if step.output_as:
                pl.context[step.output_as] = step.result

    async def _run_parallel(self, pl: Pipeline):
        async def _exec_and_push(step, prompt):
            await self._exec_step(pl, step, prompt)
            await self._webhook_step(pl, step)

        tasks = []
        for step in pl.steps:
            prompt = self._render(step.prompt_template, pl.context)
            tasks.append(_exec_and_push(step, prompt))
        await asyncio.gather(*tasks)
        failed = [s for s in pl.steps if s.status == "failed"]
        if failed:
            pl.status = "failed"
            pl.error = "; ".join(f"{s.agent}: {s.error}" for s in failed)

    async def _run_race(self, pl: Pipeline):
        async def _race_step(step, prompt):
            await self._exec_step(pl, step, prompt)
            if step.status == "completed":
                return step
            return None

        tasks = []
        for step in pl.steps:
            prompt = self._render(step.prompt_template, pl.context)
            tasks.append(asyncio.create_task(_race_step(step, prompt)))

        winner = None
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for t in done:
            result = t.result()
            if result:
                winner = result
                break

        for t in pending:
            t.cancel()

        if not winner:
            # All done tasks failed, wait for remaining
            if pending:
                done2, _ = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
                for t in done2:
                    try:
                        result = t.result()
                        if result:
                            winner = result
                            break
                    except asyncio.CancelledError:
                        pass

        if winner:
            await self._webhook_step(pl, winner)
        else:
            pl.status = "failed"
            pl.error = "all agents failed"

    async def _run_random(self, pl: Pipeline):
        chosen = random.choice(pl.steps)
        prompt = self._render(chosen.prompt_template, pl.context)
        await self._exec_step(pl, chosen, prompt)
        await self._webhook_step(pl, chosen)
        for step in pl.steps:
            if step is not chosen:
                step.status = "skipped"
        if chosen.status == "failed":
            pl.status = "failed"
            pl.error = f"step {chosen.agent} failed: {chosen.error}"

    async def _exec_step(self, pl: Pipeline, step: PipelineStep, prompt: str):
        step.status = "running"
        step.started_at = time.time()
        step_idx = pl.steps.index(step)
        session_id = f"pipeline-{pl.pipeline_id}-{step.agent}-{step_idx}"
        cfg = self._agents_cfg.get(step.agent, {})
        if cfg.get("mode") == "pty":
            await self._exec_step_pty(step, prompt, cfg)
        else:
            await self._exec_step_acp(step, prompt, session_id)
        step.completed_at = time.time()
        log.info("step_done: pipeline=%s agent=%s status=%s duration=%.1fs",
                 pl.pipeline_id, step.agent, step.status,
                 step.completed_at - step.started_at)

    async def _exec_step_acp(self, step: PipelineStep, prompt: str, session_id: str):
        parts = []
        try:
            conn = await self._pool.get_or_create(step.agent, session_id)
            async for notification in conn.session_prompt(prompt):
                if "_prompt_result" in notification:
                    if "error" in notification["_prompt_result"]:
                        step.error = str(notification["_prompt_result"]["error"])
                        step.status = "failed"
                    else:
                        step.status = "completed"
                    break
                event = transform_notification(notification)
                if event and event["type"] == "message.part":
                    parts.append(event["content"])
        except (PoolExhaustedError, AcpError) as e:
            step.error = str(e)
            step.status = "failed"
        except Exception as e:
            step.error = str(e)
            step.status = "failed"
        step.result = "".join(parts)

    async def _exec_step_pty(self, step: PipelineStep, prompt: str, cfg: dict):
        command = cfg["command"]
        args = cfg.get("args", [])
        idle_timeout = cfg.get("idle_timeout", 300)
        env = os.environ.copy()
        env.update({"TERM": "dumb", "NO_COLOR": "1", "LANG": "en_US.UTF-8"})
        env.update(cfg.get("env", {}))
        parts = []
        try:
            proc = await asyncio.create_subprocess_exec(
                command, *args, prompt,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                stdin=asyncio.subprocess.DEVNULL,
                cwd=cfg.get("working_dir", "/tmp"), env=env,
            )
            while True:
                try:
                    line = await asyncio.wait_for(proc.stdout.readline(), timeout=idle_timeout)
                except asyncio.TimeoutError:
                    proc.kill()
                    await proc.wait()
                    step.error = f"agent timeout (idle {idle_timeout}s)"
                    step.status = "failed"
                    return
                if not line:
                    break
                text = ANSI_RE.sub("", line.decode()).rstrip("\n")
                if text:
                    parts.append(text + "\n")
            await proc.wait()
            step.status = "completed" if proc.returncode == 0 else "failed"
            if proc.returncode != 0:
                stderr = (await proc.stderr.read()).decode().strip()
                step.error = stderr or f"exit code {proc.returncode}"
        except Exception as e:
            step.error = str(e)
            step.status = "failed"
        step.result = "".join(parts)

    @staticmethod
    def _render(template: str, context: dict) -> str:
        for key, val in context.items():
            template = template.replace("{{" + key + "}}", str(val))
        return template
