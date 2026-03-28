"""SQLite job persistence store."""

import json
import sqlite3
import time
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    job_id       TEXT PRIMARY KEY,
    agent        TEXT NOT NULL,
    session_id   TEXT NOT NULL,
    prompt       TEXT NOT NULL,
    cwd          TEXT DEFAULT '',
    status       TEXT DEFAULT 'pending',
    result       TEXT DEFAULT '',
    error        TEXT DEFAULT '',
    tools        TEXT DEFAULT '[]',
    created_at   REAL NOT NULL,
    completed_at REAL DEFAULT 0,
    callback_url TEXT DEFAULT '',
    callback_meta TEXT DEFAULT '{}',
    webhook_sent INTEGER DEFAULT 0,
    retries      INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
CREATE INDEX IF NOT EXISTS idx_jobs_created ON jobs(created_at);
"""


class JobStore:
    def __init__(self, db_path: str = "data/jobs.db"):
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(db_path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.executescript(_SCHEMA)
        self._migrate()

    def _migrate(self):
        cols = {r[1] for r in self._db.execute("PRAGMA table_info(jobs)").fetchall()}
        if "retries" not in cols:
            self._db.execute("ALTER TABLE jobs ADD COLUMN retries INTEGER DEFAULT 0")
            self._db.commit()

    def save(self, job) -> None:
        self._db.execute(
            """INSERT OR REPLACE INTO jobs
               (job_id, agent, session_id, prompt, cwd, status, result, error,
                tools, created_at, completed_at, callback_url, callback_meta, webhook_sent, retries)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (job.job_id, job.agent, job.session_id, job.prompt, job.cwd,
             job.status, job.result, job.error,
             json.dumps(job.tools), job.created_at, job.completed_at,
             job.callback_url, json.dumps(job.callback_meta), int(job.webhook_sent),
             job.retries),
        )
        self._db.commit()

    def load_incomplete(self) -> list[dict]:
        """Load jobs that were pending/running when Bridge last shut down."""
        rows = self._db.execute(
            "SELECT * FROM jobs WHERE status IN ('pending', 'running')"
        ).fetchall()
        return [self._row_to_dict(r) for r in rows]

    def load_unsent_webhooks(self) -> list[dict]:
        """Load completed/failed jobs whose webhook was never sent."""
        rows = self._db.execute(
            "SELECT * FROM jobs WHERE status IN ('completed', 'failed') AND webhook_sent = 0 AND callback_url != ''"
        ).fetchall()
        return [self._row_to_dict(r) for r in rows]

    def load_recent(self, limit: int = 50) -> list[dict]:
        rows = self._db.execute(
            "SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
        return [self._row_to_dict(r) for r in rows]

    def delete_old(self, max_age: float = 86400) -> int:
        cutoff = time.time() - max_age
        cur = self._db.execute(
            "DELETE FROM jobs WHERE completed_at > 0 AND completed_at < ?", (cutoff,)
        )
        self._db.commit()
        return cur.rowcount

    def close(self):
        self._db.close()

    @staticmethod
    def _row_to_dict(row: sqlite3.Row) -> dict:
        d = dict(row)
        d["tools"] = json.loads(d["tools"])
        d["callback_meta"] = json.loads(d["callback_meta"])
        d["webhook_sent"] = bool(d["webhook_sent"])
        d.setdefault("retries", 0)
        return d


_CHAT_SCHEMA = """
CREATE TABLE IF NOT EXISTS chat_messages (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id   TEXT NOT NULL,
    agent        TEXT NOT NULL,
    role         TEXT NOT NULL,
    content      TEXT NOT NULL,
    job_id       TEXT DEFAULT '',
    created_at   REAL NOT NULL,
    folded       INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_chat_session ON chat_messages(session_id);
CREATE INDEX IF NOT EXISTS idx_chat_created ON chat_messages(created_at);
"""


class ChatStore:
    def __init__(self, db_path: str = "data/jobs.db"):
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(db_path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.executescript(_CHAT_SCHEMA)

    def save_message(self, session_id: str, agent: str, role: str,
                     content: str, job_id: str = "") -> int:
        cur = self._db.execute(
            "INSERT INTO chat_messages (session_id, agent, role, content, job_id, created_at) VALUES (?,?,?,?,?,?)",
            (session_id, agent, role, content, job_id, time.time()),
        )
        self._db.commit()
        return cur.lastrowid

    def fold_session(self, session_id: str):
        self._db.execute(
            "UPDATE chat_messages SET folded = 1 WHERE session_id = ? AND folded = 0",
            (session_id,),
        )
        self._db.commit()

    def load_recent(self, limit: int = 200) -> list[dict]:
        rows = self._db.execute(
            "SELECT * FROM chat_messages ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in reversed(rows)]

    def delete_old(self, max_age: float = 7 * 86400) -> int:
        cutoff = time.time() - max_age
        cur = self._db.execute("DELETE FROM chat_messages WHERE created_at < ?", (cutoff,))
        self._db.commit()
        return cur.rowcount

    def clear_all(self) -> int:
        cur = self._db.execute("DELETE FROM chat_messages")
        self._db.commit()
        return cur.rowcount
