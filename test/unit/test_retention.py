"""v0.41.0 — data retention + caching behaviors added by the perf pass."""

import time

from src.store import ChatStore, PipelineStore


class _FakeStep:
    agent = "kiro"
    prompt_template = "p"
    output_as = ""
    timeout = 600
    status = "completed"
    result = "r"
    error = ""
    started_at = 1.0
    completed_at = 2.0


class _FakePl:
    pipeline_id = "pl-old"
    mode = "sequence"
    status = "completed"
    steps = [_FakeStep()]
    context = {}
    error = ""
    webhook_meta = {}
    created_at = 1.0
    completed_at = 2.0  # far in the past
    retries = 0


def test_pipeline_delete_old_prunes_conversation_log(tmp_path):
    store = PipelineStore(str(tmp_path / "t.db"))
    store.save(_FakePl())
    store.save_event("pl-old", "pipeline_done", {"x": 1})
    store.save_turn("pl-old", 1, "kiro", "hello", 0.5)

    deleted = store.delete_old(max_age=60)  # completed_at=2.0 is ancient

    assert deleted == 1
    db = store._db
    assert db.execute("SELECT COUNT(*) FROM pipeline_events").fetchone()[0] == 0
    assert db.execute("SELECT COUNT(*) FROM conversation_log").fetchone()[0] == 0


def test_chat_delete_old(tmp_path):
    store = ChatStore(str(tmp_path / "t.db"))
    store.save_message("s1", "kiro", "user", "old msg")
    # Backdate the row so it exceeds retention
    store._db.execute("UPDATE chat_messages SET created_at = 1.0")
    store._db.commit()
    assert store.delete_old(max_age=60) == 1


def test_llm_usage_delete_old(tmp_path, monkeypatch):
    from src.routes import litellm_proxy as lp
    monkeypatch.setattr(lp, "_DB_PATH", str(tmp_path / "usage.db"))
    monkeypatch.setattr(lp, "_db", None)
    db = lp._ensure_db()
    db.execute("INSERT INTO llm_usage (ts, model) VALUES (1.0, 'old')")
    db.execute("INSERT INTO llm_usage (ts, model) VALUES (?, 'new')", (time.time(),))
    db.commit()

    assert lp.delete_old(max_age=30 * 86400) == 1
    rows = db.execute("SELECT model FROM llm_usage").fetchall()
    assert [r["model"] for r in rows] == ["new"]


def test_busy_timeout_set(tmp_path):
    store = PipelineStore(str(tmp_path / "t.db"))
    assert store._db.execute("PRAGMA busy_timeout").fetchone()[0] == 5000


def test_templates_cache_invalidates_on_mtime(tmp_path, monkeypatch):
    import src.templates as tpl
    monkeypatch.setattr(tpl, "_TEMPLATES_DIR", tmp_path)
    monkeypatch.setattr(tpl, "_cache", {})
    monkeypatch.setattr(tpl, "_cache_key", ())

    (tmp_path / "a.yaml").write_text("name: a\nprompt: 'hi {{x}}'\n")
    assert [t["name"] for t in tpl.list_templates()] == ["a"]

    # Same mtime → served from cache (identity check)
    first = tpl._load_all()
    assert tpl._load_all() is first

    # New file → cache invalidated
    (tmp_path / "b.yaml").write_text("name: b\nprompt: 'yo'\n")
    names = sorted(t["name"] for t in tpl.list_templates())
    assert names == ["a", "b"]
