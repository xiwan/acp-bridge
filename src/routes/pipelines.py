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
    steps: list[PipelineStepRequest]
    context: dict = {}


def register(app, pipeline_mgr: PipelineManager | None):

    @app.post("/pipelines")
    async def submit_pipeline(req: PipelineRequest):
        if not pipeline_mgr:
            return JSONResponse({"error": "pipeline not available (no pool)"}, status_code=503)
        if req.mode not in ("sequence", "parallel", "race"):
            return JSONResponse({"error": f"invalid mode: {req.mode}"}, status_code=400)
        if not req.steps:
            return JSONResponse({"error": "steps required"}, status_code=400)
        pl = pipeline_mgr.submit(
            mode=req.mode,
            steps=[s.model_dump() for s in req.steps],
            context=req.context,
        )
        return {"pipeline_id": pl.pipeline_id, "status": pl.status, "mode": pl.mode,
                "steps": len(pl.steps)}

    @app.get("/pipelines/{pipeline_id}")
    async def get_pipeline(pipeline_id: str = PathParam(...)):
        if not pipeline_mgr:
            return JSONResponse({"error": "pipeline not available"}, status_code=503)
        pl = pipeline_mgr.get(pipeline_id)
        if not pl:
            return JSONResponse({"error": "pipeline not found"}, status_code=404)
        return pl.to_dict()

    @app.get("/pipelines")
    async def list_pipelines():
        if not pipeline_mgr:
            return {"pipelines": []}
        pls = pipeline_mgr.list_all()
        return {"pipelines": [p.to_dict() for p in pls]}
