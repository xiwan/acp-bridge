"""Unit tests for src/formatters.py — payload builders + artifact block (v0.39.0)."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.formatters import (
    GenericPayloadBuilder,
    OpenclawPayloadBuilder,
    PipelineFormatter,
    get_payload_builder,
)

# ── get_payload_builder registry ─────────────────────────


def test_registry_routes_by_format():
    assert isinstance(get_payload_builder("openclaw"), OpenclawPayloadBuilder)
    assert isinstance(get_payload_builder("generic"), GenericPayloadBuilder)


def test_unknown_format_falls_back_to_openclaw():
    # matches the pre-v0.39 else-branch in pipeline._send_webhook
    assert isinstance(get_payload_builder("slack"), OpenclawPayloadBuilder)
    assert isinstance(get_payload_builder(""), OpenclawPayloadBuilder)


def test_openclaw_payload_shape():
    out = get_payload_builder("openclaw").build_pipeline(
        "pid1", "sequence", "completed", "hello", channel="feishu", target="t1"
    )
    assert out == [
        {
            "tool": "message",
            "action": "send",
            "args": {"channel": "feishu", "target": "t1", "message": "hello"},
        }
    ]


def test_generic_payload_chunks_long_message():
    out = get_payload_builder("generic").build_pipeline(
        "pid1", "sequence", "completed", "x" * 4000, chunk_size=1800
    )
    assert len(out) == 3
    assert out[0] == {
        "pipeline_id": "pid1",
        "mode": "sequence",
        "status": "completed",
        "message": "x" * 1800,
        "part": 1,
        "total_parts": 3,
    }
    assert out[2]["part"] == 3 and len(out[2]["message"]) == 400


# ── format_done artifact block ───────────────────────────


def test_format_done_without_artifacts_unchanged():
    msg = PipelineFormatter.format_done("pid12345", "completed", 12.3)
    assert "产物" not in msg and "📦" not in msg


def test_format_done_renders_artifact_variants():
    artifacts = [
        {
            "type": "file",
            "label": "GDD",
            "pattern": "gdd-*.md",
            "path": "/ws/gdd-ab.md",
            "url": "https://s3/presigned",
            "exists": True,
        },
        {"type": "file", "label": "本地", "pattern": "x.md", "path": "/ws/x.md", "exists": True},
        {"type": "file", "label": "缺失", "pattern": "nope-*.zip", "exists": False},
    ]
    msg = PipelineFormatter.format_done("pid12345", "completed", 12.3, artifacts=artifacts)
    assert "📦" in msg
    assert "[GDD](https://s3/presigned)" in msg  # url wins
    assert "本地: `/ws/x.md`" in msg  # path fallback
    assert "缺失: 未生成" in msg and "nope-*.zip" in msg
