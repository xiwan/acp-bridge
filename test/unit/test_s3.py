"""Unit tests for S3 upload content headers (v0.43.1).

Presigned GET links must serve the right Content-Type; archives (.zip/.tgz)
additionally get Content-Disposition: attachment so browsers download instead
of rendering bytes inline (qa-evidence zips from harness-factory artifact pack).
"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from unittest.mock import MagicMock

from src import s3


def test_object_headers_common_types():
    cases = {
        "a/qa-evidence-2026-08-15.zip": "application/zip",
        "a/report.md": "text/markdown",
        "a/trace.json": "application/json",
        "a/clip.webm": "video/webm",
        "a/shot.png": "image/png",
        "a/page.html": "text/html",
    }
    for key, want in cases.items():
        assert s3._object_headers(key)["ContentType"] == want, key


def test_object_headers_unknown_falls_back_to_octet_stream():
    h = s3._object_headers("a/noext")
    assert h["ContentType"] == "application/octet-stream"
    assert "ContentDisposition" not in h


def test_object_headers_archives_get_attachment_disposition():
    for key in ("p/ev.zip", "p/ws.tgz", "p/ws.tar.gz", "p/EV.ZIP"):
        h = s3._object_headers(key)
        assert h["ContentDisposition"].startswith("attachment; filename="), key
        assert os.path.basename(key) in h["ContentDisposition"]
    # non-archives must not force download
    assert "ContentDisposition" not in s3._object_headers("p/report.md")
    assert "ContentDisposition" not in s3._object_headers("p/clip.webm")


def _armed(monkeypatch):
    """Arm the module with a mock client and return it."""
    client = MagicMock()
    client.generate_presigned_url.return_value = "https://s3.example/x?sig=1"
    monkeypatch.setattr(s3, "_available", True)
    monkeypatch.setattr(s3, "_client", lambda: client)
    return client


def test_upload_passes_extra_args(monkeypatch):
    client = _armed(monkeypatch)
    with tempfile.NamedTemporaryFile(suffix=".zip") as f:
        url = s3.upload(f.name, "artifacts/uid/ev.zip")
    assert url == "https://s3.example/x?sig=1"
    extra = client.upload_file.call_args.kwargs["ExtraArgs"]
    assert extra["ContentType"] == "application/zip"
    assert extra["ContentDisposition"] == 'attachment; filename="ev.zip"'


def test_upload_bytes_passes_content_type(monkeypatch):
    client = _armed(monkeypatch)
    assert s3.upload_bytes("steps/1/out.md", b"# hi")
    kwargs = client.put_object.call_args.kwargs
    assert kwargs["ContentType"] == "text/markdown"
    assert "ContentDisposition" not in kwargs


def test_put_bytes_passes_content_type(monkeypatch):
    client = _armed(monkeypatch)
    assert s3.put_bytes("mesh/p1/in.tgz", b"gz")
    kwargs = client.put_object.call_args.kwargs
    assert kwargs["ContentType"] == "application/x-tar"
    assert kwargs["ContentDisposition"] == 'attachment; filename="in.tgz"'
