"""Admin endpoints for the prompt_log table — cross-link querying and direct lookup."""

from fastapi import Path as PathParam, Query
from fastapi.responses import JSONResponse

from ..prompt_log import PromptStore, row_to_summary


def register(app, prompt_store: PromptStore | None = None):

    @app.get("/admin/prompts")
    async def list_prompts(
        parent_type: str = Query("", description="filter: 'job' | 'pipeline_step' | 'heartbeat'"),
        agent: str = Query("", description="filter by agent name"),
        limit: int = Query(50, ge=1, le=500),
        include: str = Query("", description="comma-separated extras: 'final'"),
    ):
        """Search prompt log across all parent types. Default omits large prompt fields."""
        if not prompt_store:
            return JSONResponse({"error": "prompt logging disabled"}, status_code=503)
        include_final = "final" in {x.strip() for x in (include or "").split(",")}
        rows = prompt_store.search(
            parent_type=parent_type or None,
            agent=agent or None,
            limit=limit,
        )
        return {
            "count": len(rows),
            "records": [row_to_summary(r, include_final) for r in rows],
        }

    @app.get("/admin/prompts/{record_id}")
    async def get_prompt(
        record_id: str = PathParam(...),
        include: str = Query("final", description="default includes the full final prompt"),
    ):
        """Look up one prompt record by id. Returns full final by default."""
        if not prompt_store:
            return JSONResponse({"error": "prompt logging disabled"}, status_code=503)
        row = prompt_store.get(record_id)
        if not row:
            return JSONResponse({"error": "record not found"}, status_code=404)
        include_final = "final" in {x.strip() for x in (include or "").split(",")}
        return row_to_summary(row, include_final)
