"""Prompt persistence — record every prompt actually sent to an agent.

Centralizes prompt logging across all 6 agent-invocation paths
(pipeline ACP/PTY/conversation, job ACP/PTY, heartbeat).

Design goals:
  - Best-effort writes. Never raise to the agent call path.
  - Configurable redaction of secrets (default ON).
  - Configurable size cap (default 1 MB per record).
  - Independent SQLite table; co-located in data/jobs.db for ops simplicity.

Public API:
    PromptStore(db_path, redact=True, max_size=1_048_576)
    PromptStore.record(parent_type, parent_id, ..., final, decorations) -> record_id
    PromptStore.get(record_id) -> dict | None
    PromptStore.list_by_parent(parent_type, parent_id) -> list[dict]
    PromptStore.search(parent_type=..., agent=..., limit=...) -> list[dict]

Schema migration: schema is created via CREATE TABLE IF NOT EXISTS, so old DBs
get the new table on first init. No destructive migrations.
"""

import json
import logging
import re
import sqlite3
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger("acp-bridge.prompt_log")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS prompt_log (
    record_id    TEXT PRIMARY KEY,
    parent_type  TEXT NOT NULL,
    parent_id    TEXT NOT NULL,
    parent_index INTEGER DEFAULT -1,
    agent        TEXT NOT NULL,
    session_id   TEXT DEFAULT '',
    cwd          TEXT DEFAULT '',
    mode         TEXT NOT NULL,
    template     TEXT NOT NULL DEFAULT '',
    rendered     TEXT NOT NULL DEFAULT '',
    final        TEXT NOT NULL DEFAULT '',
    decorations  TEXT DEFAULT '[]',
    created_at   REAL NOT NULL,
    final_len    INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_promptlog_parent
    ON prompt_log(parent_type, parent_id);
CREATE INDEX IF NOT EXISTS idx_promptlog_agent
    ON prompt_log(agent);
CREATE INDEX IF NOT EXISTS idx_promptlog_created
    ON prompt_log(created_at);
"""

# Mirror OPERATIONS.md "Sensitive Patterns" section. Each entry replaces
# the secret value with ***REDACTED***, preserving the key prefix when present
# so the log still indicates *what kind of secret* was scrubbed.
_SECRET_PATTERNS = [
    # key=value forms
    (re.compile(r'(token=)([^\s&\'"]{8,})', re.IGNORECASE), True),
    (re.compile(r'(api[._-]?key=)([^\s&\'"]{8,})', re.IGNORECASE), True),
    (re.compile(r'(password=)([^\s&\'"]+)', re.IGNORECASE), True),
    (re.compile(r'(secret=)([^\s&\'"]+)', re.IGNORECASE), True),
    (re.compile(r'(ACP_BRIDGE_TOKEN=)([^\s&\'"]{8,})'), True),
    (re.compile(r'(OPENCLAW_TOKEN=)([^\s&\'"]{8,})'), True),
    (re.compile(r'(LITELLM_API_KEY=)([^\s&\'"]{8,})'), True),
    (re.compile(r'(ANTHROPIC_API_KEY=)([^\s&\'"]{8,})'), True),
    (re.compile(r'(AWS_SECRET_ACCESS_KEY=)([^\s&\'"]+)'), True),
    # Bearer tokens in Authorization headers
    (re.compile(r'(Bearer\s+)([A-Za-z0-9._\-]{16,})', re.IGNORECASE), True),
    # AWS access key id (no prefix to preserve)
    (re.compile(r'\b(AKIA[0-9A-Z]{16})\b'), False),
]


def redact_secrets(text: str) -> str:
    """Replace credential patterns from OPERATIONS.md with ***REDACTED***."""
    if not text:
        return text
    for pat, has_prefix in _SECRET_PATTERNS:
        if has_prefix:
            text = pat.sub(lambda m: m.group(1) + '***REDACTED***', text)
        else:
            text = pat.sub('***REDACTED***', text)
    return text


@dataclass
class PromptRecord:
    """Strongly-typed representation of one prompt_log row."""
    record_id: str
    parent_type: str          # 'job' | 'pipeline_step' | 'heartbeat'
    parent_id: str            # job_id / pipeline_id / agent_name
    parent_index: int         # step idx / turn idx; -1 if N/A
    agent: str
    session_id: str
    cwd: str
    mode: str                 # 'acp' | 'pty'
    template: str
    rendered: str
    final: str
    decorations: list
    created_at: float
    final_len: int


class PromptStore:
    """SQLite-backed prompt log with best-effort writes.

    Args:
        db_path: SQLite file path. Created if missing.
        redact:  When True, apply redact_secrets() to template/rendered/final
                 before persisting. Default True.
        max_size: Per-field size cap. Strings longer than max_size are
                  truncated with a marker. Default 1 MB.
    """

    def __init__(self, db_path: str = "data/jobs.db",
                 redact: bool = True, max_size: int = 1_048_576):
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._db_path = db_path
        self._redact = redact
        self._max_size = max_size
        self._db = sqlite3.connect(db_path, check_same_thread=False)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.row_factory = sqlite3.Row
        self._db.executescript(_SCHEMA)
        log.info("prompt_log_init: db=%s redact=%s max_size=%d",
                 db_path, redact, max_size)

    def _truncate(self, s: str) -> str:
        if not s or len(s) <= self._max_size:
            return s
        marker = f"\n... [TRUNCATED at {self._max_size} bytes, original={len(s)}]"
        return s[: self._max_size - len(marker)] + marker

    def _process(self, s: str) -> str:
        s = self._truncate(s or "")
        if self._redact:
            s = redact_secrets(s)
        return s

    def record(self, *, parent_type: str, parent_id: str, agent: str, mode: str,
               parent_index: int = -1, session_id: str = "", cwd: str = "",
               template: str = "", rendered: str = "", final: str = "",
               decorations: list | None = None) -> str:
        """Persist one prompt record. Returns record_id, or "" on failure."""
        try:
            t = self._process(template)
            r = self._process(rendered or template)
            f = self._process(final or rendered or template)
            rec_id = str(uuid.uuid4())
            self._db.execute(
                """INSERT INTO prompt_log
                   (record_id, parent_type, parent_id, parent_index,
                    agent, session_id, cwd, mode,
                    template, rendered, final, decorations,
                    created_at, final_len)
                   VALUES (?,?,?,?, ?,?,?,?, ?,?,?,?, ?,?)""",
                (rec_id, parent_type, parent_id, parent_index,
                 agent, session_id, cwd, mode,
                 t, r, f, json.dumps(decorations or []),
                 time.time(), len(f)),
            )
            self._db.commit()
            return rec_id
        except Exception as e:
            # Best-effort: never propagate logging errors to the agent call path.
            log.warning("prompt_log_insert_failed: parent=%s/%s agent=%s err=%s",
                        parent_type, parent_id, agent, e)
            return ""

    def get(self, record_id: str) -> dict | None:
        try:
            row = self._db.execute(
                "SELECT * FROM prompt_log WHERE record_id=?", (record_id,)
            ).fetchone()
            return dict(row) if row else None
        except Exception as e:
            log.warning("prompt_log_get_failed: id=%s err=%s", record_id, e)
            return None

    def list_by_parent(self, parent_type: str, parent_id: str) -> list[dict]:
        try:
            rows = self._db.execute(
                """SELECT * FROM prompt_log
                   WHERE parent_type=? AND parent_id=?
                   ORDER BY created_at, parent_index""",
                (parent_type, parent_id),
            ).fetchall()
            return [dict(r) for r in rows]
        except Exception as e:
            log.warning("prompt_log_list_failed: parent=%s/%s err=%s",
                        parent_type, parent_id, e)
            return []

    def search(self, *, parent_type: str | None = None, agent: str | None = None,
               limit: int = 50) -> list[dict]:
        clauses, params = [], []
        if parent_type:
            clauses.append("parent_type=?")
            params.append(parent_type)
        if agent:
            clauses.append("agent=?")
            params.append(agent)
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        params.append(int(limit))
        try:
            rows = self._db.execute(
                f"SELECT * FROM prompt_log {where} "
                f"ORDER BY created_at DESC LIMIT ?",
                params,
            ).fetchall()
            return [dict(r) for r in rows]
        except Exception as e:
            log.warning("prompt_log_search_failed: err=%s", e)
            return []

    def cleanup_older_than(self, retention_seconds: float) -> int:
        """Delete records older than retention_seconds. Returns row count.

        Caller is responsible for scheduling. Returns 0 on error.
        """
        if retention_seconds <= 0:
            return 0
        try:
            cutoff = time.time() - retention_seconds
            cur = self._db.execute(
                "DELETE FROM prompt_log WHERE created_at < ?", (cutoff,)
            )
            self._db.commit()
            n = cur.rowcount
            if n:
                log.info("prompt_log_cleanup: deleted=%d cutoff=%.0f", n, cutoff)
            return n
        except Exception as e:
            log.warning("prompt_log_cleanup_failed: err=%s", e)
            return 0


def row_to_summary(row: dict, include_final: bool = False) -> dict:
    """Project a SQLite row to API JSON. Hides large fields by default."""
    out = {
        "record_id": row["record_id"],
        "parent_type": row["parent_type"],
        "parent_id": row["parent_id"],
        "parent_index": row["parent_index"],
        "agent": row["agent"],
        "session_id": row["session_id"],
        "cwd": row["cwd"],
        "mode": row["mode"],
        "decorations": json.loads(row.get("decorations") or "[]"),
        "final_len": row["final_len"],
        "created_at": row["created_at"],
    }
    if include_final:
        out["template"] = row["template"]
        out["rendered"] = row["rendered"]
        out["final"] = row["final"]
    return out
