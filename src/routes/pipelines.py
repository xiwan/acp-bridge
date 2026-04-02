"""Pipeline endpoints — submit, query, list."""

from fastapi import Path as PathParam
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from ..pipeline import PipelineManager


class PipelineStepRequest(BaseModel):
    agent: str
    prompt: str
    output_as: str = ""
    timeout: float = 600


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
             webhook_account_id: str = "", webhook_default_target: str = ""):

    @app.post("/pipelines")
    async def submit_pipeline(req: PipelineRequest):
        if not pipeline_mgr:
            return JSONResponse({"error": "pipeline not available (no pool)"}, status_code=503)
        if req.mode not in ("sequence", "parallel", "race", "random", "conversation"):
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

    @app.get("/pipelines")
    async def list_pipelines():
        if not pipeline_mgr:
            return {"pipelines": []}
        pls = pipeline_mgr.list_all()
        return {"pipelines": [p.to_dict() for p in pls]}
