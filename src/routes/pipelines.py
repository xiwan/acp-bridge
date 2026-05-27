"""Pipeline endpoints — submit, query, list."""

import asyncio
import json

from fastapi import Path as PathParam, Query
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

from ..pipeline import PipelineManager
from ..prompt_log import PromptStore, row_to_summary


class PipelineStepRequest(BaseModel):
    agent: str
    prompt: str
    output_as: str = ""
    timeout: float = 0


class PipelineRequest(BaseModel):
    mode: str = "sequence"
    steps: list[PipelineStepRequest] = []
    context: dict = {}
    target: str = ""
    channel: str = ""
    callback_meta: dict = {}
    # Conversation mode fields
    participants: list[str] = []
    topic: str = ""
    initial_context: str = ""
    solo: dict = {}
    config: dict = {}


def register(app, pipeline_mgr: PipelineManager | None,
             webhook_account_id: str = "", webhook_default_target: str = "",
             prompt_store: PromptStore | None = None):

    @app.post("/pipelines")
    async def submit_pipeline(req: PipelineRequest):
        if not pipeline_mgr:
            return JSONResponse({"error": "pipeline not available (no pool)"}, status_code=503)
        if req.mode not in ("sequence", "parallel", "race", "conversation"):
            return JSONResponse({"error": f"invalid mode: {req.mode}"}, status_code=400)
        if req.mode == "conversation":
            if len(req.participants) < 2:
                return JSONResponse({"error": "conversation requires at least 2 participants"}, status_code=400)
            if not req.topic:
                return JSONResponse({"error": "conversation requires a topic"}, status_code=400)
            context = req.context.copy()
            context.update({"participants": req.participants, "topic": req.topic,
                            "initial_context": req.initial_context, "solo": req.solo,
                            "config": req.config})
            steps = [PipelineStepRequest(agent=p, prompt="") for p in req.participants]
        else:
            if not req.steps:
                return JSONResponse({"error": "steps required"}, status_code=400)
            context = req.context
            steps = req.steps
        meta = req.callback_meta
        if req.target:
            meta["target"] = req.target
        elif webhook_default_target and "target" not in meta:
            meta["target"] = webhook_default_target
        if webhook_account_id and "account_id" not in meta:
            meta["account_id"] = webhook_account_id
        if req.channel:
            meta["channel"] = req.channel
        pl = pipeline_mgr.submit(
            mode=req.mode,
            steps=[s.model_dump() for s in steps],
            context=context,
            webhook_meta=meta,
        )
        resp = {"pipeline_id": pl.pipeline_id, "status": pl.status, "mode": pl.mode}
        if req.mode == "conversation":
            resp["participants"] = req.participants
            resp["topic"] = req.topic
        else:
            resp["steps"] = len(pl.steps)
        return resp

    @app.get("/pipelines/{pipeline_id}")
    async def get_pipeline(pipeline_id: str = PathParam(...)):
        if not pipeline_mgr:
            return JSONResponse({"error": "pipeline not available"}, status_code=503)
        pl = pipeline_mgr.get(pipeline_id)
        if not pl:
            return JSONResponse({"error": "pipeline not found"}, status_code=404)
        d = pl.to_dict()
        if pl.mode == "conversation":
            d["transcript"] = pipeline_mgr.get_transcript(pipeline_id)
        return d

    @app.post("/pipelines/{pipeline_id}/pause")
    async def pause_pipeline(pipeline_id: str = PathParam(...)):
        if not pipeline_mgr:
            return JSONResponse({"error": "pipeline not available"}, status_code=503)
        pl = pipeline_mgr.get(pipeline_id)
        if not pl:
            return JSONResponse({"error": "pipeline not found"}, status_code=404)
        if pl.mode != "conversation":
            return JSONResponse({"error": "pause only supported for conversation mode"}, status_code=400)
        if pl.status not in ("running", "paused"):
            return JSONResponse({"error": f"cannot pause pipeline in status: {pl.status}"}, status_code=400)
        pl._gate.clear()
        return {"pipeline_id": pipeline_id, "paused": True}

    @app.post("/pipelines/{pipeline_id}/resume")
    async def resume_pipeline(pipeline_id: str = PathParam(...)):
        if not pipeline_mgr:
            return JSONResponse({"error": "pipeline not available"}, status_code=503)
        pl = pipeline_mgr.get(pipeline_id)
        if not pl:
            return JSONResponse({"error": "pipeline not found"}, status_code=404)
        pl._gate.set()
        return {"pipeline_id": pipeline_id, "paused": False}

    @app.post("/pipelines/{pipeline_id}/inject")
    async def inject_message(pipeline_id: str = PathParam(...), req: dict = {}):
        if not pipeline_mgr:
            return JSONResponse({"error": "pipeline not available"}, status_code=503)
        pl = pipeline_mgr.get(pipeline_id)
        if not pl:
            return JSONResponse({"error": "pipeline not found"}, status_code=404)
        if pl.mode != "conversation":
            return JSONResponse({"error": "inject only supported for conversation mode"}, status_code=400)
        message = req.get("message", "").strip()
        if not message:
            return JSONResponse({"error": "message is required"}, status_code=400)
        await pl._inject_queue.put(message)
        # Auto-resume if paused
        if not pl._gate.is_set():
            pl._gate.set()
        return {"pipeline_id": pipeline_id, "injected": True, "message": message[:100]}

    @app.get("/pipelines/{pipeline_id}/events")
    async def stream_pipeline_events(pipeline_id: str = PathParam(...)):
        """Server-Sent Events stream of pipeline lifecycle events.

        On connect: replay history (events already emitted), then stream live.
        Stream closes after `pipeline_done` event.
        Heartbeat every 15s keeps proxies/load balancers from killing the connection.
        """
        if not pipeline_mgr:
            return JSONResponse({"error": "pipeline not available"}, status_code=503)
        pl = pipeline_mgr.get(pipeline_id)
        if not pl:
            return JSONResponse({"error": "pipeline not found"}, status_code=404)

        async def gen():
            # 1. Replay history
            for evt in list(pl._event_history):
                yield f"event: {evt['event']}\ndata: {json.dumps(evt['data'])}\n\n"

            # 2. If already finished, close immediately
            if pl.status in ("completed", "failed"):
                return

            # 3. Subscribe to live stream
            q: asyncio.Queue = asyncio.Queue(maxsize=1000)
            pl._event_subs.add(q)
            try:
                while True:
                    try:
                        evt = await asyncio.wait_for(q.get(), timeout=15.0)
                    except asyncio.TimeoutError:
                        # Heartbeat keeps connection alive
                        import time as _t
                        yield f"event: heartbeat\ndata: {json.dumps({'ts': _t.time()})}\n\n"
                        continue
                    if evt is None:
                        # End-of-stream sentinel posted by _run on completion
                        return
                    yield f"event: {evt['event']}\ndata: {json.dumps(evt['data'])}\n\n"
            finally:
                pl._event_subs.discard(q)

        return StreamingResponse(
            gen(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",  # disable nginx buffering
            },
        )

    @app.get("/pipelines/{pipeline_id}/steps/{step_index}/live")
    async def get_step_live(pipeline_id: str = PathParam(...), step_index: int = PathParam(...)):
        if not pipeline_mgr:
            return JSONResponse({"error": "pipeline not available"}, status_code=503)
        pl = pipeline_mgr.get(pipeline_id)
        if not pl:
            return JSONResponse({"error": "pipeline not found"}, status_code=404)
        if step_index < 0 or step_index >= len(pl.steps):
            return JSONResponse({"error": "step index out of range"}, status_code=400)
        step = pl.steps[step_index]
        content = step.result if step.status in ("completed", "failed") else "".join(step._live_parts)
        return {"pipeline_id": pipeline_id, "step": step_index, "agent": step.agent,
                "status": step.status, "content": content, "parts_count": len(step._live_parts)}

    @app.get("/pipelines/{pipeline_id}/prompts")
    async def get_pipeline_prompts(
        pipeline_id: str = PathParam(...),
        include: str = Query("", description="comma-separated extras: 'final' to include full prompt fields"),
    ):
        """Return prompt_log records for a pipeline (one per step / conversation
        turn). Default response omits large prompt fields; pass ?include=final
        to also return template/rendered/final."""
        if not prompt_store:
            return JSONResponse({"error": "prompt logging disabled"}, status_code=503)
        include_final = "final" in {x.strip() for x in (include or "").split(",")}
        rows = prompt_store.list_by_parent("pipeline_step", pipeline_id)
        return {
            "pipeline_id": pipeline_id,
            "records": [row_to_summary(r, include_final) for r in rows],
        }

    @app.get("/pipelines/{pipeline_id}/artifacts")
    async def list_artifacts(pipeline_id: str = PathParam(...)):
        import os
        if not pipeline_mgr:
            return JSONResponse({"error": "pipeline not available"}, status_code=503)
        pl = pipeline_mgr.get(pipeline_id)
        if not pl:
            return JSONResponse({"error": "pipeline not found"}, status_code=404)
        shared_cwd = pl.context.get("shared_cwd", "")
        if not shared_cwd or not os.path.isdir(shared_cwd):
            return {"pipeline_id": pipeline_id, "shared_cwd": shared_cwd, "files": []}
        files = []
        for root, dirs, filenames in os.walk(shared_cwd):
            # Skip hidden dirs
            dirs[:] = [d for d in dirs if not d.startswith(".")]
            for f in filenames:
                if f.startswith("."):
                    continue
                full = os.path.join(root, f)
                rel = os.path.relpath(full, shared_cwd)
                files.append({"path": rel, "size": os.path.getsize(full)})
        return {"pipeline_id": pipeline_id, "shared_cwd": shared_cwd, "files": files}

    @app.get("/pipelines/{pipeline_id}/artifacts/download")
    async def download_artifact(pipeline_id: str = PathParam(...), path: str = ""):
        """Download a specific file from pipeline's shared_cwd."""
        import os
        from starlette.responses import FileResponse
        if not pipeline_mgr:
            return JSONResponse({"error": "pipeline not available"}, status_code=503)
        pl = pipeline_mgr.get(pipeline_id)
        if not pl:
            return JSONResponse({"error": "pipeline not found"}, status_code=404)
        shared_cwd = pl.context.get("shared_cwd", "")
        if not shared_cwd or not path:
            return JSONResponse({"error": "invalid path"}, status_code=400)
        # Prevent path traversal
        full = os.path.realpath(os.path.join(shared_cwd, path))
        if not full.startswith(os.path.realpath(shared_cwd)):
            return JSONResponse({"error": "forbidden"}, status_code=403)
        if not os.path.isfile(full):
            return JSONResponse({"error": "not found"}, status_code=404)
        return FileResponse(full)

    @app.get("/pipelines")
    async def list_pipelines():
        if not pipeline_mgr:
            return {"pipelines": []}
        pls = pipeline_mgr.list_all()
        return {"pipelines": [p.to_dict() for p in pls]}

    @app.get("/stats/pipelines")
    async def stats_pipelines(hours: float = 24):
        if not pipeline_mgr:
            return {"period_hours": hours, "modes": {}}
        return pipeline_mgr.stats(hours=hours)
