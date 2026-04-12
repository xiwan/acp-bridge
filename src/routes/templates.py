"""Template endpoints."""

from fastapi import Request
from fastapi.responses import JSONResponse

from ..templates import list_templates, render


def register(app):

    @app.get("/templates")
    async def get_templates():
        return {"templates": list_templates()}

    @app.post("/templates/render")
    async def render_template(request: Request):
        body = await request.json()
        name = body.get("name", "")
        vars = body.get("vars", {})
        if not name:
            return JSONResponse({"error": "name is required"}, status_code=400)
        result = render(name, vars)
        if "error" in result:
            return JSONResponse(result, status_code=404)
        return result
