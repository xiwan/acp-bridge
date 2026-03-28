"""Job endpoints — submit, query, list."""

import uuid as _uuid

from fastapi import Path as PathParam
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.responses import Response

from ..jobs import JobManager


class JobRequest(BaseModel):
    agent_name: str
    session_id: str = ""
    prompt: str
    cwd: str = ""
    callback_url: str = ""
    callback_meta: dict = {}
    target: str = ""
    discord_target: str = ""
    channel: str = ""


def register(app, job_mgr: JobManager | None, webhook_account_id: str, webhook_default_target: str):

    @app.post("/jobs")
    async def submit_job(req: JobRequest):
        if not job_mgr:
            return JSONResponse({"error": "no pool configured"}, status_code=500)
        sid = req.session_id or str(_uuid.uuid5(_uuid.NAMESPACE_DNS, req.agent_name))
        meta = req.callback_meta
        effective_target = req.target or req.discord_target
        if effective_target:
            meta["target"] = effective_target
        elif webhook_default_target and "target" not in meta:
            meta["target"] = webhook_default_target
        if webhook_account_id and "account_id" not in meta:
            meta["account_id"] = webhook_account_id
        if req.channel:
            meta["channel"] = req.channel
        job = job_mgr.submit(req.agent_name, sid, req.prompt,
                             req.callback_url, meta, cwd=req.cwd)
        return {"job_id": job.job_id, "status": job.status, "agent": job.agent, "session_id": sid}

    @app.get("/jobs/{job_id}")
    async def get_job(job_id: str = PathParam(...)):
        if not job_mgr:
            return JSONResponse({"error": "no pool configured"}, status_code=500)
        job = job_mgr.get(job_id)
        if not job:
            return JSONResponse({"error": "job not found"}, status_code=404)
        return job.to_dict()

    @app.get("/jobs/{job_id}/result")
    async def get_job_result(job_id: str = PathParam(...)):
        if not job_mgr:
            return JSONResponse({"error": "no pool configured"}, status_code=500)
        job = job_mgr.get(job_id)
        if not job:
            return JSONResponse({"error": "job not found"}, status_code=404)
        return Response(content=job.result or "", media_type="text/markdown; charset=utf-8")

    @app.get("/jobs")
    async def list_jobs():
        if not job_mgr:
            return {"jobs": []}
        jobs = job_mgr.list_jobs()
        summary = {"pending": 0, "running": 0, "completed": 0, "failed": 0}
        dicts = []
        for j in jobs:
            dicts.append(j.to_dict())
            if j.status in summary:
                summary[j.status] += 1
        return {"jobs": dicts, "summary": summary}
