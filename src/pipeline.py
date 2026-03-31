"""Multi-agent pipeline — sequence, parallel, race execution."""

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field

import httpx

from .acp_client import AcpError, AcpProcessPool, PoolExhaustedError
from .sse import transform_notification

log = logging.getLogger("acp-bridge.pipeline")


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
                 webhook_url: str = "", webhook_token: str = ""):
        self._pool = pool
        self._agents_cfg = agents_cfg
        self._pipelines: dict[str, Pipeline] = {}
        self._webhook_url = webhook_url
        self._webhook_token = webhook_token
        self._http: httpx.AsyncClient | None = None

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
        asyncio.create_task(self._run(pl))
        log.info("pipeline_submitted: id=%s mode=%s steps=%d", pl.pipeline_id, mode, len(pl.steps))
        return pl

    def get(self, pipeline_id: str) -> Pipeline | None:
        return self._pipelines.get(pipeline_id)

    def list_all(self, limit: int = 50) -> list[Pipeline]:
        return sorted(self._pipelines.values(), key=lambda p: p.created_at, reverse=True)[:limit]

    async def _run(self, pl: Pipeline):
        pl.status = "running"
        try:
            if pl.mode == "sequence":
                await self._run_sequence(pl)
            elif pl.mode == "parallel":
                await self._run_parallel(pl)
            elif pl.mode == "race":
                await self._run_race(pl)
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
        log.info("pipeline_done: id=%s status=%s duration=%.1fs",
                 pl.pipeline_id, pl.status, pl.completed_at - pl.created_at)
        if pl.webhook_meta.get("target"):
            await self._webhook(pl)

    async def _webhook(self, pl: Pipeline):
        url = self._webhook_url
        if not url:
            return
        target = pl.webhook_meta.get("target", "")
        channel = pl.webhook_meta.get("channel", "discord")
        account_id = pl.webhook_meta.get("account_id", "")

        # Build summary message
        dur = round(pl.completed_at - pl.created_at, 1)
        agents = " → ".join(s.agent for s in pl.steps) if pl.mode == "sequence" else \
                 " | ".join(s.agent for s in pl.steps)
        lines = [f"🔗 **Pipeline** ({pl.mode}) — `{pl.pipeline_id[:8]}`",
                 f"> Agents: {agents}"]
        if pl.status == "failed":
            lines.append(f"> ❌ {pl.error}")
        else:
            lines.append(f"> ✅ Completed in {dur}s")
        for s in pl.steps:
            if s.result:
                preview = s.result[:300] + "..." if len(s.result) > 300 else s.result
                lines.append(f">\n> **{s.agent}** ({round(s.completed_at - s.started_at, 1)}s):")
                for ln in preview.splitlines():
                    lines.append(f"> {ln}")

        payload = {"tool": "message", "action": "send",
                   "args": {"channel": channel, "target": target,
                            "message": "\n".join(lines)}}
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
            if resp.status_code != 200:
                log.warning("pipeline_webhook_rejected: id=%s body=%s",
                            pl.pipeline_id, resp.text[:300])
        except Exception as e:
            log.error("pipeline_webhook_failed: id=%s error=%s", pl.pipeline_id, e)

    async def _run_sequence(self, pl: Pipeline):
        for step in pl.steps:
            prompt = self._render(step.prompt_template, pl.context)
            await self._exec_step(pl, step, prompt)
            if step.status == "failed":
                pl.status = "failed"
                pl.error = f"step {step.agent} failed: {step.error}"
                return
            if step.output_as:
                pl.context[step.output_as] = step.result

    async def _run_parallel(self, pl: Pipeline):
        tasks = []
        for step in pl.steps:
            prompt = self._render(step.prompt_template, pl.context)
            tasks.append(self._exec_step(pl, step, prompt))
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

        if not winner:
            pl.status = "failed"
            pl.error = "all agents failed"

    async def _exec_step(self, pl: Pipeline, step: PipelineStep, prompt: str):
        step.status = "running"
        step.started_at = time.time()
        step_idx = pl.steps.index(step)
        session_id = f"pipeline-{pl.pipeline_id}-{step.agent}-{step_idx}"
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
        step.completed_at = time.time()
        log.info("step_done: pipeline=%s agent=%s status=%s duration=%.1fs",
                 pl.pipeline_id, step.agent, step.status,
                 step.completed_at - step.started_at)

    @staticmethod
    def _render(template: str, context: dict) -> str:
        for key, val in context.items():
            template = template.replace("{{" + key + "}}", str(val))
        return template
