"""Tests for src/prompt_log.py — PromptStore, redact_secrets, row_to_summary.

These mirror the scratch validation in test/scratch/try_prompt_log.py but
exercise the production module and integrate with pytest infrastructure.
"""

import json
import os
import tempfile
import time

import pytest

from src.prompt_log import PromptStore, redact_secrets, row_to_summary


@pytest.fixture
def store():
    """Fresh PromptStore on a temp DB; auto-cleanup after test."""
    db = tempfile.NamedTemporaryFile(delete=False, suffix=".db").name
    s = PromptStore(db, redact=False, max_size=1_048_576)
    yield s
    try:
        os.unlink(db)
    except FileNotFoundError:
        pass


@pytest.fixture
def redacting_store():
    db = tempfile.NamedTemporaryFile(delete=False, suffix=".db").name
    s = PromptStore(db, redact=True, max_size=1_048_576)
    yield s
    try:
        os.unlink(db)
    except FileNotFoundError:
        pass


# -------- redact_secrets() --------

class TestRedact:
    @pytest.mark.parametrize("raw,key_prefix", [
        ("token=abc123def456ghi789", "token="),
        ("API_KEY=supersecretvalue", "API_KEY="),
        ("password=hunter2", "password="),
        ("secret=tellnobody", "secret="),
        ("ACP_BRIDGE_TOKEN=abcdefghij1234", "ACP_BRIDGE_TOKEN="),
        ("OPENCLAW_TOKEN=abcdefgh12345678", "OPENCLAW_TOKEN="),
        ("LITELLM_API_KEY=sk-12345678", "LITELLM_API_KEY="),
        ("ANTHROPIC_API_KEY=ant-abcdefgh", "ANTHROPIC_API_KEY="),
        ("AWS_SECRET_ACCESS_KEY=wJalrXUtnFEMI/K7", "AWS_SECRET_ACCESS_KEY="),
        ("Authorization: Bearer eyJhbGciOiJIUzI1NiI", "Bearer "),
    ])
    def test_redacts_with_prefix_preserved(self, raw, key_prefix):
        out = redact_secrets(raw)
        assert "***REDACTED***" in out
        assert key_prefix in out

    def test_redacts_aws_access_key_id(self):
        out = redact_secrets("uses key AKIAIOSFODNN7EXAMPLE in code")
        assert "***REDACTED***" in out
        assert "AKIA" not in out

    def test_benign_text_untouched(self):
        text = "this is a normal prompt with no secrets in it"
        assert redact_secrets(text) == text

    def test_empty_string(self):
        assert redact_secrets("") == ""

    def test_none_safe(self):
        assert redact_secrets(None) is None


# -------- PromptStore.record() --------

class TestRecord:
    def test_round_trip(self, store):
        rid = store.record(
            parent_type="pipeline_step", parent_id="pipe-1",
            parent_index=2, agent="opengame", session_id="sess-1",
            cwd="/tmp/og", mode="acp",
            template="hello {{name}}",
            rendered="hello world",
            final="[ws hint] hello world [suffix]",
            decorations=["shared_workspace_zh", "prompt_suffix"],
        )
        assert rid
        row = store.get(rid)
        assert row is not None
        assert row["agent"] == "opengame"
        assert row["parent_index"] == 2
        assert row["template"] == "hello {{name}}"
        assert row["rendered"] == "hello world"
        assert "[ws hint]" in row["final"]
        assert "[suffix]" in row["final"]
        assert json.loads(row["decorations"]) == ["shared_workspace_zh", "prompt_suffix"]
        assert row["final_len"] == len(row["final"])

    def test_defaults_rendered_and_final_to_template(self, store):
        rid = store.record(parent_type="job", parent_id="j1", agent="kiro",
                            mode="acp", template="bare prompt")
        row = store.get(rid)
        # When rendered/final omitted, they fall back to template
        assert row["rendered"] == "bare prompt"
        assert row["final"] == "bare prompt"

    def test_db_failure_returns_empty_string(self, store):
        # Force a failure by closing the connection
        store._db.close()
        rid = store.record(parent_type="job", parent_id="j-fail",
                            agent="kiro", mode="acp", final="hello")
        assert rid == ""

    def test_redact_applied_when_enabled(self, redacting_store):
        rid = redacting_store.record(
            parent_type="job", parent_id="j-redact", agent="kiro", mode="acp",
            final="curl -H 'Authorization: Bearer eyJhbGciOiJIUzI1NiI'",
        )
        row = redacting_store.get(rid)
        assert "eyJhbGc" not in row["final"]
        assert "***REDACTED***" in row["final"]

    def test_redact_skipped_when_disabled(self, store):
        secret = "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"
        rid = store.record(parent_type="job", parent_id="j-noredact",
                            agent="kiro", mode="acp", final=secret)
        row = store.get(rid)
        # store fixture has redact=False
        assert "eyJhbGc" in row["final"]


# -------- truncation --------

class TestTruncate:
    def test_oversize_clipped_with_marker(self):
        db = tempfile.NamedTemporaryFile(delete=False, suffix=".db").name
        try:
            s = PromptStore(db, redact=False, max_size=1000)
            big = "X" * 5000
            rid = s.record(parent_type="job", parent_id="j-big", agent="kiro",
                           mode="acp", final=big)
            row = s.get(rid)
            assert len(row["final"]) <= 1000
            assert "[TRUNCATED" in row["final"]
            assert row["final_len"] == len(row["final"])
        finally:
            os.unlink(db)

    def test_undersize_unchanged(self, store):
        small = "small prompt"
        rid = store.record(parent_type="job", parent_id="j-small",
                            agent="kiro", mode="acp", final=small)
        row = store.get(rid)
        assert row["final"] == small


# -------- list/search --------

class TestQuery:
    def test_list_by_parent_ordered(self, store):
        for i in range(3):
            store.record(parent_type="pipeline_step", parent_id="pipe-multi",
                         parent_index=i, agent=f"agent{i}", mode="acp",
                         final=f"step {i}")
            time.sleep(0.005)
        rows = store.list_by_parent("pipeline_step", "pipe-multi")
        assert len(rows) == 3
        assert [r["parent_index"] for r in rows] == [0, 1, 2]

    def test_list_by_parent_isolates_other_pipelines(self, store):
        store.record(parent_type="pipeline_step", parent_id="pipe-A",
                     agent="kiro", mode="acp", final="A")
        store.record(parent_type="pipeline_step", parent_id="pipe-B",
                     agent="kiro", mode="acp", final="B")
        rows_a = store.list_by_parent("pipeline_step", "pipe-A")
        assert len(rows_a) == 1
        assert rows_a[0]["final"] == "A"

    def test_search_by_agent(self, store):
        store.record(parent_type="job", parent_id="j1", agent="kiro",
                     mode="acp", final="...")
        store.record(parent_type="job", parent_id="j2", agent="claude",
                     mode="acp", final="...")
        store.record(parent_type="heartbeat", parent_id="kiro", agent="kiro",
                     mode="acp", final="...")
        kiro_rows = store.search(agent="kiro")
        assert len(kiro_rows) == 2

    def test_search_combined_filters(self, store):
        store.record(parent_type="job", parent_id="j1", agent="kiro",
                     mode="acp", final="...")
        store.record(parent_type="heartbeat", parent_id="kiro", agent="kiro",
                     mode="acp", final="...")
        rows = store.search(parent_type="job", agent="kiro")
        assert len(rows) == 1
        assert rows[0]["parent_type"] == "job"


# -------- row_to_summary() --------

class TestSummary:
    def test_summary_excludes_final_by_default(self, store):
        rid = store.record(parent_type="job", parent_id="j-sum", agent="kiro",
                            mode="acp", template="t", rendered="r", final="f")
        row = store.get(rid)
        out = row_to_summary(row, include_final=False)
        assert "final" not in out
        assert "template" not in out
        assert "rendered" not in out
        assert out["final_len"] == 1
        assert out["agent"] == "kiro"
        assert out["decorations"] == []

    def test_summary_includes_final_when_requested(self, store):
        rid = store.record(parent_type="job", parent_id="j-sum2", agent="kiro",
                            mode="acp", template="t", rendered="r", final="f")
        row = store.get(rid)
        out = row_to_summary(row, include_final=True)
        assert out["final"] == "f"
        assert out["template"] == "t"
        assert out["rendered"] == "r"


# -------- cleanup --------

class TestCleanup:
    def test_cleanup_removes_old_records(self, store):
        # Insert one record then directly age it via SQL
        rid = store.record(parent_type="job", parent_id="j-old",
                           agent="kiro", mode="acp", final="ancient")
        store._db.execute("UPDATE prompt_log SET created_at=? WHERE record_id=?",
                          (time.time() - 7200, rid))
        store._db.commit()
        n = store.cleanup_older_than(retention_seconds=3600)
        assert n == 1
        assert store.get(rid) is None

    def test_cleanup_zero_retention_noop(self, store):
        store.record(parent_type="job", parent_id="j-keep", agent="kiro",
                     mode="acp", final="...")
        assert store.cleanup_older_than(retention_seconds=0) == 0
