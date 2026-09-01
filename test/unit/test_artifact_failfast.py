"""
Unit tests for sequence-pipeline artifact fail-fast:
a step that declares a `type: file` artifact but does not produce the file
must fail the step (and the pipeline) instead of letting downstream steps
run against stale or missing inputs.
"""

from unittest.mock import Mock

import pytest

from src.acp_client import AcpProcessPool
from src.pipeline import Pipeline, PipelineManager, PipelineStep


@pytest.fixture
def manager(tmp_path):
    pool = Mock(spec=AcpProcessPool)
    pool._connections = {}
    agents_cfg = {
        "kiro": {"command": "echo", "working_dir": "/tmp", "description": "kiro", "mode": "acp"},
    }
    return PipelineManager(pool=pool, agents_cfg=agents_cfg, db_path=str(tmp_path / "test.db"))


def _pipeline(tmp_path, artifacts):
    return Pipeline(
        pipeline_id="test-ff",
        mode="sequence",
        steps=[PipelineStep(agent="kiro", prompt_template="write gdd")],
        context={"shared_cwd": str(tmp_path), "_artifacts": artifacts},
    )


def test_missing_file_artifact_detected(manager, tmp_path):
    pl = _pipeline(tmp_path, [{"type": "file", "label": "GDD", "pattern": "gdd.md"}])
    err = manager._check_step_artifact(pl, 0)
    assert "gdd.md" in err
    assert "deliverable missing" in err


def test_present_file_artifact_passes(manager, tmp_path):
    (tmp_path / "gdd.md").write_text("# gdd")
    pl = _pipeline(tmp_path, [{"type": "file", "label": "GDD", "pattern": "gdd.md"}])
    assert manager._check_step_artifact(pl, 0) == ""


def test_empty_file_artifact_detected(manager, tmp_path):
    # A 0-byte deliverable (e.g. a failed remote fs_write) is as useless as a
    # missing one — mesh relay can merge back empty files that must not pass.
    (tmp_path / "gdd.md").write_text("")
    pl = _pipeline(tmp_path, [{"type": "file", "label": "GDD", "pattern": "gdd.md"}])
    err = manager._check_step_artifact(pl, 0)
    assert "gdd.md" in err


def test_glob_pattern_matches(manager, tmp_path):
    (tmp_path / "ab12cd34.html").write_text("<html>")
    pl = _pipeline(tmp_path, [{"type": "file", "label": "game", "pattern": "*.html"}])
    assert manager._check_step_artifact(pl, 0) == ""


def test_url_artifact_not_checked(manager, tmp_path):
    pl = _pipeline(tmp_path, [{"type": "url", "label": "URL", "pattern": "https://cdn.example.com"}])
    assert manager._check_step_artifact(pl, 0) == ""


def test_step_without_declaration_not_checked(manager, tmp_path):
    pl = _pipeline(tmp_path, [None])
    assert manager._check_step_artifact(pl, 0) == ""
    # index beyond declaration list is also fine
    assert manager._check_step_artifact(pl, 5) == ""


def test_no_shared_cwd_skips_check(manager, tmp_path):
    pl = _pipeline(tmp_path, [{"type": "file", "label": "GDD", "pattern": "gdd.md"}])
    pl.context["shared_cwd"] = ""
    assert manager._check_step_artifact(pl, 0) == ""
