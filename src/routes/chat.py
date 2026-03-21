"""Chat history endpoints (Web UI only)."""

import os

from pydantic import BaseModel
from starlette.responses import FileResponse
from starlette.staticfiles import StaticFiles

from ..store import ChatStore


def register(app, config: dict):
    static_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "static")
    if not os.path.isdir(static_dir):
        return

    app.mount("/static", StaticFiles(directory=static_dir), name="static")

    @app.get("/ui")
    async def ui():
        return FileResponse(os.path.join(static_dir, "index.html"))

    db_path = config.get("server", {}).get("db_path", "data/jobs.db")
    chat_store = ChatStore(db_path)

    class ChatMsg(BaseModel):
        session_id: str
        agent: str
        role: str
        content: str
        job_id: str = ""

    @app.post("/chat/messages")
    async def save_chat_message(msg: ChatMsg):
        mid = chat_store.save_message(msg.session_id, msg.agent, msg.role, msg.content, msg.job_id)
        return {"id": mid}

    @app.post("/chat/fold")
    async def fold_chat_session(req: dict):
        chat_store.fold_session(req.get("session_id", ""))
        return {"status": "ok"}

    @app.get("/chat/messages")
    async def load_chat_messages(limit: int = 200):
        return {"messages": chat_store.load_recent(limit)}

    @app.delete("/chat/messages")
    async def clear_chat_messages():
        n = chat_store.clear_all()
        return {"deleted": n}
