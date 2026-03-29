"""File upload endpoints."""

import os
import re
import logging

from fastapi import UploadFile, File, Form
from fastapi.responses import JSONResponse

log = logging.getLogger("acp-bridge.files")

_SAFE_NAME = re.compile(r"[^\w\-.]")


def _sanitize(name: str) -> str:
    name = os.path.basename(name)
    name = _SAFE_NAME.sub("_", name)
    return name or "upload"


def register(app, upload_dir: str, max_size: int = 3 * 1024 * 1024):
    os.makedirs(upload_dir, exist_ok=True)

    @app.post("/files")
    async def upload_file(file: UploadFile = File(...), agent: str = Form("")):
        name = _sanitize(file.filename or "upload")
        data = await file.read()
        if len(data) > max_size:
            return JSONResponse({"error": f"file too large ({len(data)} bytes, max {max_size})"}, status_code=413)
        dest = os.path.join(upload_dir, name)
        # Avoid overwrite: append suffix
        if os.path.exists(dest):
            base, ext = os.path.splitext(name)
            i = 1
            while os.path.exists(dest):
                dest = os.path.join(upload_dir, f"{base}_{i}{ext}")
                i += 1
            name = os.path.basename(dest)
        with open(dest, "wb") as f:
            f.write(data)
        log.info("uploaded: %s size=%d agent=%s", name, len(data), agent or "(none)")
        return {"filename": name, "path": dest, "size": len(data)}

    @app.get("/files")
    async def list_files():
        files = []
        for name in sorted(os.listdir(upload_dir)):
            fp = os.path.join(upload_dir, name)
            if os.path.isfile(fp):
                files.append({"filename": name, "path": fp, "size": os.path.getsize(fp)})
        return {"files": files, "upload_dir": upload_dir}

    @app.delete("/files/{filename}")
    async def delete_file(filename: str):
        name = _sanitize(filename)
        fp = os.path.join(upload_dir, name)
        if not os.path.isfile(fp):
            return JSONResponse({"error": "not found"}, status_code=404)
        os.remove(fp)
        log.info("deleted: %s", name)
        return {"status": "deleted", "filename": name}

    @app.get("/files/{filename}/download")
    async def download_file(filename: str):
        from starlette.responses import FileResponse
        name = _sanitize(filename)
        fp = os.path.join(upload_dir, name)
        if not os.path.isfile(fp):
            return JSONResponse({"error": "not found"}, status_code=404)
        return FileResponse(fp)
