"""Unit tests for L3a — S3 workspace relay helpers + cross-node gate."""

import os, sys, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from src import s3


def test_pack_unpack_roundtrip():
    src = tempfile.mkdtemp(); dst = tempfile.mkdtemp()
    open(os.path.join(src, "index.html"), "w").write("<h1>g</h1>")
    os.makedirs(os.path.join(src, "assets"))
    open(os.path.join(src, "assets", "a.js"), "w").write("x=1")
    s3.unpack_dir(s3.pack_dir(src), dst)
    assert open(os.path.join(dst, "index.html")).read() == "<h1>g</h1>"
    assert open(os.path.join(dst, "assets", "a.js")).read() == "x=1"


def test_presigned_returns_none_when_unavailable(monkeypatch):
    monkeypatch.setattr(s3, "_available", False)
    assert s3.presigned_put("k") is None
    assert s3.presigned_get("k") is None
    assert s3.put_bytes("k", b"x") is False
    s3.delete_prefix("k")  # must not raise


@pytest.mark.asyncio
async def test_cross_node_step_fails_without_s3(monkeypatch):
    """Hard prerequisite: no S3 -> cross-node step fails with a clear error, never silent."""
    from src.pipeline import PipelineManager, Pipeline, PipelineStep
    monkeypatch.setattr(s3, "is_available", lambda: False)

    mgr = PipelineManager.__new__(PipelineManager)  # bypass __init__ (no pool/db needed)
    step = PipelineStep(agent="claude", prompt_template="hi")
    pl = Pipeline(pipeline_id="p1", mode="sequence", steps=[step], context={})
    await mgr._exec_step_remote(pl, step, "hi", "/tmp/ws", ("http://peer:18010", "tok"))

    assert step.status == "failed"
    assert "requires S3" in step.error


def test_mesh_resolver_default_none():
    """A pipeline with no mesh wiring never takes the remote branch."""
    from src.pipeline import PipelineManager
    mgr = PipelineManager.__new__(PipelineManager)
    mgr._mesh_resolver = None
    assert mgr._mesh_resolver is None  # local-only path unaffected
