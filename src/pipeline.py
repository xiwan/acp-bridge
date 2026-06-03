"""Multi-agent pipeline — sequence, parallel, race execution."""

import asyncio
import json
import logging
import os
import re
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from .acp_client import AcpError, AcpProcessPool, PoolExhaustedError
from .formatters import PipelineFormatter, get_template, get_prompt_suffix
from .prompt_log import PromptStore
from .sse import transform_notification
from .store import PipelineStore
from .utils import run_pty_subprocess
from .webhook import WebhookSender, chunk_text

log = logging.getLogger("acp-bridge.pipeline")

AGENT_ICONS = {"kiro": "🟢", "claude": "🟣", "codex": "🔵", "qwen": "🟠", "opencode": "⚪"}
_SEPARATOR = get_template("components", "separator", "━━━━━━━━━━━━━━━━━━━━")
MAX_OUTPUT_SIZE = 1 * 1024 * 1024  # 1 MB — truncate step output beyond this
MENTION_RE = re.compile(r"@(\w+)")
_VAR_RE = re.compile(r"\{\{(\w+)\}\}")

_PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"


def _load_prompt(name: str) -> str:
    return (_PROMPTS_DIR / name).read_text().strip()


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
    # Tools invoked during this step. Each entry: {"id", "name", "status"}.
    # Populated from session/update tool_call + tool_call_update notifications.
    tools: list = field(default_factory=list)
    _live_parts: list = field(default_factory=list, repr=False)
    # Accumulated agent_thought_chunk text. Joined for /steps/{i}/live.
    _thinking_parts: list = field(default_factory=list, repr=False)

    def to_dict(self) -> dict:
        d = {"agent": self.agent, "status": self.status}
        if self.result:
            d["result"] = self.result
        if self.error:
            d["error"] = self.error
        if self.completed_at and self.started_at:
            d["duration"] = round(self.completed_at - self.started_at, 1)
        if self.tools:
            d["tools"] = self.tools
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
    # Human-in-the-loop controls
    _gate: asyncio.Event = field(default_factory=asyncio.Event, repr=False)
    _inject_queue: asyncio.Queue = field(default_factory=asyncio.Queue, repr=False)
    # SSE event stream (v0.21.0): _event_history is replayed to late subscribers,
    # _event_subs is the set of live asyncio.Queues fed by _emit_event().
    _event_history: list = field(default_factory=list, repr=False)
    _event_subs: set = field(default_factory=set, repr=False)

    def __post_init__(self):
        self._gate.set()  # starts unpaused

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
        d["shared_cwd"] = self.context.get("shared_cwd", "")
        d["paused"] = not self._gate.is_set()
        if self.mode == "conversation":
            d["participants"] = self.context.get("participants", [])
            d["topic"] = self.context.get("topic", "")
            d["initial_context"] = self.context.get("initial_context", "")
            d["config"] = self.context.get("config", {})
            d["turns"] = self.context.get("turns", 0)
            d["stop_reason"] = self.context.get("stop_reason", "")
            if self.context.get("output"):
                d["output"] = self.context["output"]
        return d


class PipelineManager:
    def __init__(self, pool: AcpProcessPool, agents_cfg: dict,
                 webhook_url: str = "", webhook_token: str = "",
                 webhook_format: str = "openclaw", webhook_secret: str = "",
                 db_path: str = "data/jobs.db",
                 prompt_store: PromptStore | None = None):
        self._pool = pool
        self._agents_cfg = agents_cfg
        self._pipelines: dict[str, Pipeline] = {}
        self._sender = WebhookSender(
            default_url=webhook_url, default_token=webhook_token,
            default_format=webhook_format, default_secret=webhook_secret,
        )
        self._store = PipelineStore(db_path)
        self._prompt_store = prompt_store
        # L3: optional mesh hook. (agent) -> (peer_url, mesh_token) if the agent is a
        # remote skill that should run on a peer with S3 workspace relay, else None.
        self._mesh_resolver = None

    def _make_shared_cwd(self, pl: Pipeline) -> str:
        """Create and return a shared workspace directory for the pipeline.
        If context already contains a valid shared_cwd, reuse it (enables cross-pipeline inheritance).
        """
        existing = pl.context.get("shared_cwd", "")
        if existing and os.path.isdir(existing):
            return existing
        base = self._agents_cfg.get("_public_workdir",
                   self._agents_cfg.get("_conversation_workdir", "/tmp/acp-pipelines"))
        if pl.mode == "conversation":
            shared_cwd = os.path.join(base, "conversation", f"conv-{pl.pipeline_id[:8]}")
        else:
            shared_cwd = os.path.join(base, pl.mode, f"pipeline-{pl.pipeline_id[:8]}")
        os.makedirs(shared_cwd, exist_ok=True)
        pl.context["shared_cwd"] = shared_cwd
        return shared_cwd

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

    def get_transcript(self, pipeline_id: str) -> list[dict]:
        return self._store.load_transcript(pipeline_id)

    def cleanup(self, max_age: float = 3600) -> int:
        """Remove completed pipelines older than max_age from in-memory cache."""
        now = time.time()
        stale = [pid for pid, pl in self._pipelines.items()
                 if pl.completed_at > 0 and now - pl.completed_at > max_age]
        for pid in stale:
            del self._pipelines[pid]
        return len(stale)

    def list_all(self, limit: int = 50) -> list[Pipeline]:
        seen = set(self._pipelines.keys())
        pls = list(self._pipelines.values())
        for d in self._store.load_recent(limit):
            if d["pipeline_id"] not in seen:
                pls.append(self._dict_to_pipeline(d))
        return sorted(pls, key=lambda p: p.created_at, reverse=True)[:limit]

    def stats(self, hours: float = 24) -> dict:
        """Aggregate pipeline counts/success/duration by mode over a time window."""
        cutoff = time.time() - hours * 3600
        by_mode: dict[str, dict] = {}
        for d in self._store.load_recent(500):
            if d["created_at"] < cutoff:
                continue
            m = d["mode"]
            s = by_mode.setdefault(m, {"total": 0, "completed": 0, "failed": 0, "running": 0, "durations": []})
            s["total"] += 1
            st = d["status"]
            if st == "completed":
                s["completed"] += 1
                if d["completed_at"]:
                    s["durations"].append(d["completed_at"] - d["created_at"])
            elif st == "failed":
                s["failed"] += 1
            elif st == "running":
                s["running"] += 1
        out = {}
        for m, s in by_mode.items():
            durs = s.pop("durations")
            s["avg_duration"] = round(sum(durs) / len(durs), 2) if durs else 0
            s["max_duration"] = round(max(durs), 2) if durs else 0
            out[m] = s
        return {"period_hours": hours, "modes": out}

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
            shared_cwd = self._make_shared_cwd(pl)
            log.info("pipeline_cwd: id=%s mode=%s cwd=%s", pl.pipeline_id, pl.mode, shared_cwd)
            self._emit_event(pl, "pipeline_started", {
                "pipeline_id": pl.pipeline_id,
                "mode": pl.mode,
                "steps": len(pl.steps),
                "shared_cwd": shared_cwd,
                "agents": [s.agent for s in pl.steps],
            })
            await self._webhook_start(pl)
            if pl.mode == "sequence":
                await self._run_sequence(pl)
            elif pl.mode == "parallel":
                await self._run_parallel(pl)
            elif pl.mode == "race":
                if not pl.steps:
                    raise ValueError("Race mode requires at least one step")
                await self._run_race(pl)
            elif pl.mode == "conversation":
                await self._run_conversation(pl)
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
        self._emit_event(pl, "pipeline_done", {
            "pipeline_id": pl.pipeline_id,
            "status": pl.status,
            "duration": round(pl.completed_at - pl.created_at, 1),
            "error": pl.error,
        })
        # Signal end-of-stream to live subscribers
        for q in list(pl._event_subs):
            try:
                q.put_nowait(None)
            except asyncio.QueueFull:
                pass
        await self._webhook(pl)

        # --- Auto-chain: if `next` is defined and pipeline succeeded, submit next ---
        if pl.status == "completed" and pl.context.get("next"):
            await self._auto_chain(pl)

    async def _auto_chain(self, pl: Pipeline):
        """Auto-submit the next pipeline, inheriting shared_cwd and output."""
        next_def = pl.context["next"]
        if not isinstance(next_def, dict) or "mode" not in next_def:
            log.warning("auto_chain_skip: invalid next definition in pipeline=%s", pl.pipeline_id)
            return

        # Build context: inherit shared_cwd + output from current pipeline
        next_context = next_def.get("context", {}).copy()
        next_context.setdefault("shared_cwd", pl.context.get("shared_cwd", ""))
        if pl.context.get("output"):
            next_context.setdefault("output", pl.context["output"])

        # Build steps — support dynamic steps from output
        steps = next_def.get("steps", [])
        if not steps and next_def.get("steps_from_output") and pl.context.get("output"):
            # Generate steps from conversation output (e.g. tasks array)
            output = pl.context["output"]
            tasks = output.get("tasks", []) if isinstance(output, dict) else []
            for task in tasks:
                agent = task.get("agent", "")
                module = task.get("module", "")
                files = task.get("files", [])
                if agent:
                    prompt_tpl = next_def.get("step_prompt_template",
                                              "在 {shared_cwd} 中实现 {module}，负责文件: {files}")
                    prompt = prompt_tpl.format(
                        shared_cwd=next_context.get("shared_cwd", ""),
                        module=module, files=", ".join(files) if files else module,
                        agent=agent)
                    steps.append({"agent": agent, "prompt": prompt})

        if not steps:
            log.warning("auto_chain_skip: no steps resolved for pipeline=%s", pl.pipeline_id)
            return

        next_pl = self.submit(
            mode=next_def["mode"],
            steps=steps,
            context=next_context,
            webhook_meta=pl.webhook_meta.copy(),
        )
        pl.context["next_pipeline_id"] = next_pl.pipeline_id
        self._store.save(pl)
        log.info("auto_chain: %s -> %s mode=%s steps=%d",
                 pl.pipeline_id, next_pl.pipeline_id, next_def["mode"], len(steps))

    _CHUNK_SIZE = 1800

    def _emit_event(self, pl: Pipeline, event_type: str, data: dict) -> None:
        """Push a lifecycle event to all SSE subscribers and store in history.

        Late subscribers replay history on connect, then receive live events.
        Each event is stamped with `_emitted_at` (unix seconds) so clients
        can render correct timestamps on late-connect replay.
        """
        data = {**data, "_emitted_at": time.time()}
        evt = {"event": event_type, "data": data}
        pl._event_history.append(evt)
        for q in list(pl._event_subs):
            try:
                q.put_nowait(evt)
            except asyncio.QueueFull:
                # Subscriber too slow — drop it so we don't stall pipeline execution
                log.warning("sse_subscriber_dropped: pipeline=%s queue_full", pl.pipeline_id)
                pl._event_subs.discard(q)

    async def _send_webhook(self, pl: Pipeline, message: str):
        """Send a single message payload via webhook."""
        url = self._sender.default_url
        if not url:
            return
        target = pl.webhook_meta.get("target", "")
        channel = pl.webhook_meta.get("channel", "discord")
        account_id = pl.webhook_meta.get("account_id", "")
        fmt = pl.webhook_meta.get("format", self._sender.default_format)
        secret = pl.webhook_meta.get("secret", self._sender._secret)

        if fmt == "generic":
            parts = chunk_text(message, self._CHUNK_SIZE)
            payloads = [{"pipeline_id": pl.pipeline_id, "mode": pl.mode,
                         "status": pl.status, "message": p,
                         "part": i+1, "total_parts": len(parts)}
                        for i, p in enumerate(parts)]
        else:
            payloads = [{"tool": "message", "action": "send",
                         "args": {"channel": channel, "target": target, "message": message}}]

        await self._sender.send(
            url, payloads, secret=secret,
            account_id=account_id, channel=channel,
            log_prefix=f"pipeline_webhook id={pl.pipeline_id}",
        )

    async def _webhook_start(self, pl: Pipeline):
        """Push a notification when pipeline starts."""
        if not self._sender.default_url or not pl.webhook_meta.get("target"):
            return
        agents = [s.agent for s in pl.steps]
        if pl.mode == "conversation":
            agents = pl.context.get("participants", agents)
        msg = PipelineFormatter.format_start(pl.pipeline_id, pl.mode, agents)
        await self._send_webhook(pl, msg)

    async def _webhook_step(self, pl: Pipeline, step: PipelineStep):
        """Push a single step result immediately after it completes."""
        if not self._sender.default_url or not pl.webhook_meta.get("target"):
            return
        idx = pl.steps.index(step) + 1
        dur = round(step.completed_at - step.started_at, 1)
        msg = PipelineFormatter.format_step(
            pl.pipeline_id, idx, len(pl.steps), step.agent, dur,
            step.status, result=step.result, error=step.error)
        await self._send_webhook(pl, msg)

    async def _webhook(self, pl: Pipeline):
        """Final summary push — overall status + per-step duration breakdown."""
        if not self._sender.default_url or not pl.webhook_meta.get("target"):
            return
        dur = round(pl.completed_at - pl.created_at, 1)
        steps_data = [{"agent": s.agent, "status": s.status,
                       "started_at": s.started_at, "completed_at": s.completed_at}
                      for s in pl.steps]
        msg = PipelineFormatter.format_done(
            pl.pipeline_id, pl.status, dur, error=pl.error, steps=steps_data)
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
        shared_cwd = pl.context.get("shared_cwd", "")

        async def _exec_and_push(step, prompt, step_cwd):
            await self._exec_step(pl, step, prompt, cwd_override=step_cwd)
            await self._webhook_step(pl, step)

        tasks = []
        for step in pl.steps:
            prompt = self._render(step.prompt_template, pl.context)
            step_cwd = os.path.join(shared_cwd, step.agent) if shared_cwd else ""
            if step_cwd:
                os.makedirs(step_cwd, exist_ok=True)
            tasks.append(_exec_and_push(step, prompt, step_cwd))
        await asyncio.gather(*tasks)
        failed = [s for s in pl.steps if s.status == "failed"]
        if failed:
            pl.status = "failed"
            pl.error = "; ".join(f"{s.agent}: {s.error}" for s in failed)

    async def _run_race(self, pl: Pipeline):
        shared_cwd = pl.context.get("shared_cwd", "")

        async def _race_step(step, prompt, step_cwd):
            await self._exec_step(pl, step, prompt, cwd_override=step_cwd)
            if step.status == "completed":
                return step
            return None

        tasks = []
        for step in pl.steps:
            prompt = self._render(step.prompt_template, pl.context)
            step_cwd = os.path.join(shared_cwd, step.agent) if shared_cwd else ""
            if step_cwd:
                os.makedirs(step_cwd, exist_ok=True)
            tasks.append(asyncio.create_task(_race_step(step, prompt, step_cwd)))

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

    async def _run_conversation(self, pl: Pipeline):
        participants = pl.context.get("participants", [])
        topic = pl.context.get("topic", "")
        initial_context = pl.context.get("initial_context", "")
        config = pl.context.get("config", {})
        max_turns = config.get("max_turns", 10)
        turn_timeout = config.get("turn_timeout_seconds", 120)
        stop_conditions = config.get("stop_conditions", ["DONE"])
        no_progress_threshold = config.get("no_progress_threshold", 2)
        a2a_rules = config.get("a2a_rules", True)
        output_schema = config.get("output_schema")  # optional JSON schema hint
        solo = pl.context.get("solo", {})

        shared_cwd = pl.context.get("shared_cwd", "")  # already created by _run
        log.info("conv_start: pipeline=%s shared_cwd=%s participants=%s",
                 pl.pipeline_id, shared_cwd, participants)

        # Build agent descriptions from metadata
        agent_descs = []
        for name in participants:
            cfg = self._agents_cfg.get(name, {})
            agent_descs.append(f"- {name}: {cfg.get('description', '')}")
        participants_block = "\n".join(agent_descs)

        # Detect language from topic — Chinese if contains CJK chars
        has_cjk = any('\u4e00' <= c <= '\u9fff' for c in topic)

        if has_cjk:
            a2a_block = _load_prompt("a2a_rules_zh.txt") if a2a_rules else ""
        else:
            a2a_block = _load_prompt("a2a_rules.txt") if a2a_rules else ""

        no_progress_count = 0
        agent_index = 0
        last_output = ""
        last_agent = ""
        transcript = []
        seen_agents = set()

        for turn in range(1, max_turns + 1):
            # --- Human-in-the-loop: pause gate ---
            if not pl._gate.is_set():
                pl.status = "paused"
                self._store.save(pl)
                log.info("conv_paused: pipeline=%s turn=%d", pl.pipeline_id, turn)
                await pl._gate.wait()
                pl.status = "running"

            # --- Human-in-the-loop: inject message ---
            injected = None
            if not pl._inject_queue.empty():
                injected = await pl._inject_queue.get()

            current_agent = participants[agent_index]
            session_id = f"conv-{pl.pipeline_id}-{current_agent}"
            first_turn_for_agent = current_agent not in seen_agents
            seen_agents.add(current_agent)

            if injected:
                # Injected message replaces this turn — record as [Human] turn
                output = injected
                duration = 0.0
                current_agent_label = "Human"
                transcript.append({"turn": turn, "agent": current_agent_label,
                                   "content": output, "duration": duration})
                self._store.save_turn(pl.pipeline_id, turn, current_agent_label, output, duration)
                log.info("conv_inject: pipeline=%s turn=%d content=%s",
                         pl.pipeline_id, turn, output[:80])
                await self._webhook_conversation_turn(pl, turn, current_agent_label, output, duration)
                last_output = output
                last_agent = current_agent_label
                # Don't advance agent_index — next turn same agent responds to human
                continue

            # Build prompt — each agent gets topic+rules on their first turn
            # PTY agents get it every turn (no session memory)
            is_pty = self._agents_cfg.get(current_agent, {}).get("mode") == "pty"
            if first_turn_for_agent or is_pty:
                tpl = _load_prompt("conversation_first_turn_zh.txt") if has_cjk else _load_prompt("conversation_first_turn.txt")
                prompt = tpl.format(topic=topic, participants=participants_block,
                                    agent=current_agent, shared_cwd=shared_cwd)
                if solo.get(current_agent):
                    prompt += f"\n[SOLO] {solo[current_agent]}\n"
                if initial_context and first_turn_for_agent:
                    prompt += f"\n{initial_context}\n"
                prompt += a2a_block
                if last_output:
                    prompt += f"\n\n[{last_agent}]: {last_output}"
            else:
                prompt = f"[{last_agent}]: {last_output}"

            # Execute
            started = time.time()
            output = await self._exec_conversation_turn(
                current_agent, session_id, prompt, turn_timeout, cwd=shared_cwd,
                pl=pl, turn_idx=turn)
            duration = round(time.time() - started, 1)

            # Record
            transcript.append({"turn": turn, "agent": current_agent,
                               "content": output, "duration": duration})
            self._store.save_turn(pl.pipeline_id, turn, current_agent, output, duration)
            log.info("conv_turn: pipeline=%s turn=%d agent=%s duration=%.1fs",
                     pl.pipeline_id, turn, current_agent, duration)

            # Webhook per turn
            await self._webhook_conversation_turn(pl, turn, current_agent, output, duration)

            # Check stop conditions
            upper = output.upper()
            if "DONE" in stop_conditions and "STATUS: DONE" in upper:
                pl.context["stop_reason"] = "DONE"
                break
            if "CONSENSUS" in stop_conditions and "STATUS: CONSENSUS" in upper:
                pl.context["stop_reason"] = "CONSENSUS"
                break

            # No progress detection
            if output.strip().upper() in ("PASS", "NOTHING TO ADD", ""):
                no_progress_count += 1
                if "NO_PROGRESS" in stop_conditions and no_progress_count >= no_progress_threshold:
                    pl.context["stop_reason"] = "NO_PROGRESS"
                    break
            else:
                no_progress_count = 0

            last_output = output
            last_agent = current_agent

            # Next agent: check @mention or round-robin
            mention = MENTION_RE.search(output)
            if mention and mention.group(1) in participants and mention.group(1) != current_agent:
                agent_index = participants.index(mention.group(1))
            else:
                agent_index = (agent_index + 1) % len(participants)
        else:
            pl.context["stop_reason"] = "MAX_TURNS"

        pl.context["transcript"] = transcript
        pl.context["turns"] = len(transcript)

        # --- Output extraction: extract structured JSON from final turn ---
        if output_schema and transcript:
            self._extract_output(pl, transcript)

        pl.status = "completed"

    async def _exec_conversation_turn(self, agent: str, session_id: str,
                                       prompt: str, timeout: float,
                                       cwd: str = "",
                                       pl: Pipeline | None = None,
                                       turn_idx: int = -1) -> str:
        cfg = self._agents_cfg.get(agent, {})
        final_prompt = prompt + get_prompt_suffix()

        ps = getattr(self, '_prompt_store', None)
        if ps and pl is not None:
            ps.record(
                parent_type="pipeline_step", parent_id=pl.pipeline_id,
                parent_index=turn_idx, agent=agent,
                mode=cfg.get("mode", "acp"), session_id=session_id, cwd=cwd,
                template=prompt, rendered=prompt, final=final_prompt,
                decorations=["conversation_turn", "prompt_suffix"],
            )

        if cfg.get("mode") == "pty":
            step = PipelineStep(agent=agent, prompt_template="")
            pty_cfg = {**cfg, "working_dir": cwd} if cwd else cfg
            await self._exec_step_pty(step, final_prompt, pty_cfg)
            return step.result
        # ACP mode
        parts = []
        prompt_result = None
        try:
            conn = await self._pool.get_or_create(agent, session_id, cwd=cwd)
            async for notification in conn.session_prompt(final_prompt, idle_timeout=timeout):
                if "_prompt_result" in notification:
                    prompt_result = notification["_prompt_result"]
                    break
                event = transform_notification(notification)
                if not event:
                    continue
                if event["type"] == "message.part":
                    parts.append(event["content"])
                # Emit step_progress so SSE clients see thinking/tool events per turn.
                # `index` here is the turn index (negative if not provided).
                if pl is not None:
                    self._emit_event(pl, "step_progress", {
                        "index": turn_idx,
                        "agent": agent,
                        "kind": event["type"],
                        **{k: v for k, v in event.items() if k != "type"},
                    })
        except (PoolExhaustedError, AcpError) as e:
            return f"[ERROR] {e}"
        except Exception as e:
            return f"[ERROR] {e}"

        output = "".join(parts)

        # Fallback: extract from prompt result if streaming yielded nothing
        if not output and prompt_result:
            result = prompt_result.get("result", {})
            for msg in result.get("messages", []):
                for part in msg.get("parts", []):
                    text = part.get("content", "") or part.get("text", "")
                    if text:
                        output += text

        if not output:
            log.warning("conv_turn_empty: agent=%s session=%s", agent, session_id)

        return output

    async def _webhook_conversation_turn(self, pl: Pipeline, turn: int,
                                          agent: str, content: str, duration: float):
        if not self._sender.default_url or not pl.webhook_meta.get("target"):
            return
        msg = PipelineFormatter.format_turn(pl.pipeline_id, turn, agent, content, duration)
        await self._send_webhook(pl, msg)

    _JSON_BLOCK_RE = re.compile(r'```json\s*\n(.*?)\n```', re.DOTALL)
    _JSON_OBJ_RE = re.compile(r'(\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\})', re.DOTALL)

    def _extract_output(self, pl: Pipeline, transcript: list[dict]):
        """Extract structured JSON output from the final agent turn (not Human)."""
        # Find last non-Human turn
        for entry in reversed(transcript):
            if entry["agent"] == "Human":
                continue
            content = entry["content"]
            # Try ```json block first
            m = self._JSON_BLOCK_RE.search(content)
            if m:
                try:
                    pl.context["output"] = json.loads(m.group(1))
                    return
                except json.JSONDecodeError:
                    pass
            # Fallback: find JSON object in text
            m = self._JSON_OBJ_RE.search(content)
            if m:
                try:
                    pl.context["output"] = json.loads(m.group(1))
                    return
                except json.JSONDecodeError:
                    pass
            break  # only check the last agent turn

    async def _exec_step(self, pl: Pipeline, step: PipelineStep, prompt: str, cwd_override: str | None = None):
        step.status = "running"
        step.started_at = time.time()
        step_idx = pl.steps.index(step)
        session_id = f"pipeline-{pl.pipeline_id}-{step.agent}-{step_idx}"
        shared_cwd = cwd_override if cwd_override is not None else pl.context.get("shared_cwd", "")

        # Prompt at this point is post-render (var substitution already applied
        # by the caller). Capture it as `rendered` before further decoration.
        rendered_prompt = prompt
        decorations: list[str] = []

        self._emit_event(pl, "step_started", {
            "index": step_idx,
            "agent": step.agent,
            "prompt_preview": (prompt[:200] + "...") if len(prompt) > 200 else prompt,
        })

        # Inject shared workspace hint for non-conversation modes
        if shared_cwd and pl.mode != "conversation":
            has_cjk = any('\u4e00' <= c <= '\u9fff' for c in prompt)
            ws_template = "shared_workspace_zh.txt" if has_cjk else "shared_workspace.txt"
            ws_prompt = _load_prompt(ws_template)
            prompt = ws_prompt.format(shared_cwd=shared_cwd) + "\n\n" + prompt
            decorations.append(ws_template.replace(".txt", ""))

        cfg = self._agents_cfg.get(step.agent, {})
        timeout = step.timeout or cfg.get("idle_timeout", 300)
        final_prompt = prompt + get_prompt_suffix()
        decorations.append("prompt_suffix")

        ps = getattr(self, '_prompt_store', None)
        if ps:
            ps.record(
                parent_type="pipeline_step", parent_id=pl.pipeline_id,
                parent_index=step_idx, agent=step.agent, mode=cfg.get("mode", "acp"),
                session_id=session_id, cwd=shared_cwd,
                template=step.prompt_template,
                rendered=rendered_prompt,
                final=final_prompt,
                decorations=decorations,
            )

        try:
            mesh_target = self._mesh_resolver(step.agent) if self._mesh_resolver else None
            if mesh_target and shared_cwd:
                await asyncio.wait_for(
                    self._exec_step_remote(pl, step, final_prompt, shared_cwd, mesh_target),
                    timeout=timeout)
            elif cfg.get("mode") == "pty":
                pty_cfg = {**cfg, "working_dir": shared_cwd} if shared_cwd else cfg
                await asyncio.wait_for(
                    self._exec_step_pty(step, final_prompt, pty_cfg),
                    timeout=timeout)
            else:
                await asyncio.wait_for(
                    self._exec_step_acp(pl, step, step_idx, final_prompt, session_id, cwd=shared_cwd),
                    timeout=timeout)
        except asyncio.TimeoutError:
            step.error = f"step timeout ({timeout}s)"
            step.status = "failed"
            log.warning("step_timeout: pipeline=%s agent=%s timeout=%ds",
                        pl.pipeline_id, step.agent, timeout)
        step.completed_at = time.time()
        # Truncate oversized output to prevent OOM
        if len(step.result) > MAX_OUTPUT_SIZE:
            original_len = len(step.result)
            step.result = step.result[:MAX_OUTPUT_SIZE] + f'\n... (truncated {original_len - MAX_OUTPUT_SIZE} bytes)'
            log.warning("step_output_truncated: pipeline=%s agent=%s original=%d limit=%d",
                        pl.pipeline_id, step.agent, original_len, MAX_OUTPUT_SIZE)
        log.info("step_done: pipeline=%s agent=%s status=%s duration=%.1fs",
                 pl.pipeline_id, step.agent, step.status,
                 step.completed_at - step.started_at)

        evt_type = "step_completed" if step.status == "completed" else "step_failed"
        evt_data = {
            "index": step_idx,
            "agent": step.agent,
            "duration": round(step.completed_at - step.started_at, 1),
            "status": step.status,
        }
        if step.status == "completed":
            evt_data["result_preview"] = (step.result[:300] + "...") if len(step.result) > 300 else step.result
        else:
            evt_data["error"] = step.error
        self._emit_event(pl, evt_type, evt_data)

    async def _exec_step_acp(self, pl: Pipeline, step: PipelineStep, step_idx: int,
                             prompt: str, session_id: str, cwd: str = ""):
        parts = []
        step._live_parts = parts
        try:
            conn = await self._pool.get_or_create(step.agent, session_id, cwd=cwd)
            async for notification in conn.session_prompt(prompt):
                if "_prompt_result" in notification:
                    if "error" in notification["_prompt_result"]:
                        step.error = str(notification["_prompt_result"]["error"])
                        step.status = "failed"
                    else:
                        step.status = "completed"
                    break
                event = transform_notification(notification)
                if not event:
                    continue
                kind = event["type"]

                # Existing behavior: cumulative message text → step.result
                if kind == "message.part":
                    parts.append(event["content"])
                # NEW: thinking accumulation
                elif kind == "message.thinking":
                    step._thinking_parts.append(event.get("content", ""))
                # NEW: tools tracking by toolCallId
                elif kind == "tool.start":
                    step.tools.append({
                        "id": event.get("toolCallId", ""),
                        "name": event.get("title", ""),
                        "status": event.get("status", "pending"),
                    })
                elif kind == "tool.done":
                    tid = event.get("toolCallId", "")
                    found = next((t for t in step.tools if t["id"] == tid), None)
                    if found:
                        found["status"] = event.get("status", "completed")
                    else:
                        # Orphan done (start lost): record for visibility
                        step.tools.append({
                            "id": tid,
                            "name": event.get("title", ""),
                            "status": event.get("status", "completed"),
                        })

                # NEW: emit live SSE event for ALL transformed kinds
                self._emit_event(pl, "step_progress", {
                    "index": step_idx,
                    "agent": step.agent,
                    "kind": kind,
                    **{k: v for k, v in event.items() if k != "type"},
                })
        except (PoolExhaustedError, AcpError) as e:
            step.error = str(e)
            step.status = "failed"
        except Exception as e:
            step.error = str(e)
            step.status = "failed"
        step.result = "".join(parts)

    async def _exec_step_remote(self, pl: Pipeline, step: PipelineStep,
                                prompt: str, shared_cwd: str, mesh_target: tuple):
        """L3 (A side): relay shared_cwd to a peer via S3, run the step there, merge back.

        S3 is a hard prerequisite — without it a cross-node step fails (never silent).
        """
        import httpx
        from src import s3
        peer_url, mesh_token = mesh_target
        if not s3.is_available():
            step.status = "failed"
            step.error = ("cross-bridge pipeline step requires S3 "
                          "(mesh workspace relay); s3 unavailable")
            log.warning("l3_no_s3: pipeline=%s agent=%s", pl.pipeline_id, step.agent)
            return
        idx = pl.steps.index(step)
        base = f"mesh-ws/{pl.pipeline_id}/step-{idx}"
        try:
            if not s3.put_bytes(f"{base}/in.tgz", s3.pack_dir(shared_cwd)):
                step.status = "failed"; step.error = "workspace upload (A->S3) failed"; return
            in_url = s3.presigned_get(f"{base}/in.tgz")
            out_url = s3.presigned_put(f"{base}/out.tgz")
            body = {"jsonrpc": "2.0", "id": 1, "method": "tasks/send",
                    "params": {"skill": step.agent,
                               "message": {"parts": [{"type": "text", "text": prompt}]},
                               "workspace_in_url": in_url, "workspace_out_url": out_url}}
            headers = {"X-A2A-Hop": "1"}
            if mesh_token:
                headers["Authorization"] = f"Bearer {mesh_token}"
            async with httpx.AsyncClient(timeout=step.timeout or 600) as c:
                r = await c.post(peer_url.rstrip("/") + "/a2a", json=body, headers=headers)
                r.raise_for_status()
                resp = r.json()
            if "error" in resp:
                step.status = "failed"; step.error = resp["error"].get("message", "remote error"); return
            # merge result workspace back into the authoritative shared_cwd
            get_out = s3.presigned_get(f"{base}/out.tgz")
            ro = httpx.get(get_out, timeout=120)
            if ro.status_code == 200 and ro.content:
                s3.unpack_dir(ro.content, shared_cwd)
            arts = resp.get("result", {}).get("artifacts", [])
            step.result = "".join(p.get("text", "") for a in arts for p in a.get("parts", []))
            step.status = "completed"
        except Exception as e:
            step.status = "failed"; step.error = f"remote step failed: {e}"
            log.warning("l3_remote_failed: pipeline=%s agent=%s err=%s",
                        pl.pipeline_id, step.agent, e)
        finally:
            # Clean up ONLY this step's own prefix. Deleting the whole pipeline prefix
            # here would race parallel steps (first finisher wipes others' in/out.tgz).
            s3.delete_prefix(f"{base}/")

    async def _exec_step_pty(self, step: PipelineStep, prompt: str, cfg: dict):
        result = await run_pty_subprocess(
            command=cfg["command"],
            args=cfg.get("args", []),
            prompt=prompt,
            cwd=cfg.get("working_dir", "/tmp"),
            env_overrides=cfg.get("env"),
            idle_timeout=cfg.get("idle_timeout", 300),
            max_duration=cfg.get("max_duration", 600),
        )
        step.status = result.status
        step.result = result.output
        step.error = result.error

    @staticmethod
    def _render(template: str, context: dict) -> str:
        return _VAR_RE.sub(lambda m: str(context.get(m.group(1), m.group(0))), template)
