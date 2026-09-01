"""Unit tests for src/render.py + POST /pipelines template rendering (v0.37.0).

Two layers:
1. Pure functions — recursive substitution, scope precedence, unresolved
   detection with JSON paths, artifact extraction/resolution.
2. Route wiring — POST /pipelines renders before submit, returns 400 on
   unresolved variables, and stays byte-identical to v0.36.2 when no
   input/vars are supplied.
"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import httpx
import pytest
from fastapi import FastAPI

from src.render import (
    build_scope,
    extract_artifacts,
    extract_chained_artifacts,
    format_missing,
    propagate_uid,
    render_payload,
    resolve_artifacts,
)
from src.routes import pipelines as pipelines_routes

# ============================================================================
# Pure functions
# ============================================================================

def test_render_is_recursive_into_nested_context():
    steps = [{"agent": "harness", "prompt": "idea: {{input}} -> gdd-{{uid}}.md"}]
    context = {
        "shared_cwd": "/tmp/opengame",
        "next": {"mode": "sequence", "steps": [
            {"agent": "qwen", "prompt": "upload to s3://{{bucket}}/{{uid}}/index.html"},
        ]},
    }
    steps, context, uid, missing = render_payload(steps, context, "racing game",
                                                  {"bucket": "b1"})
    assert missing == []
    assert steps[0]["prompt"] == f"idea: racing game -> gdd-{uid}.md"
    # 4 levels deep: context.next.steps[0].prompt
    assert context["next"]["steps"][0]["prompt"] == f"upload to s3://b1/{uid}/index.html"


def test_uid_is_consistent_across_the_whole_tree():
    steps = [{"agent": "a", "prompt": "{{uid}}"}, {"agent": "b", "prompt": "{{uid}}"}]
    context = {"next": {"steps": [{"agent": "c", "prompt": "{{uid}}"}]}}
    steps, context, uid, _ = render_payload(steps, context, "x", {})
    assert steps[0]["prompt"] == steps[1]["prompt"] == uid
    assert context["next"]["steps"][0]["prompt"] == uid


def test_uid_is_full_entropy_and_differs_per_call():
    _, _, uid1, _ = render_payload([], {}, "x", {})
    _, _, uid2, _ = render_payload([], {}, "x", {})
    assert len(uid1) == 8 and int(uid1, 16) >= 0  # 4 bytes hex
    assert uid1 != uid2


def test_scope_precedence_vars_override_auto_and_input():
    scope = build_scope("from-input", {"uid": "FORCED", "date": "1999-01-01",
                                       "input": "from-vars"})
    assert scope["uid"] == "FORCED"
    assert scope["date"] == "1999-01-01"
    assert scope["input"] == "from-vars"


def test_explicit_uid_is_honoured():
    _, _, uid, _ = render_payload([{"agent": "a", "prompt": "{{uid}}"}], {},
                                  "x", {}, uid="REUSED")
    assert uid == "REUSED"


def test_unresolved_variables_are_reported_with_paths_and_left_verbatim():
    steps = [{"agent": "a", "prompt": "ok"},
             {"agent": "b", "prompt": "{{distribution_id}} and {{nope}}"}]
    context = {"next": {"steps": [{"agent": "c", "prompt": "{{alsomissing}}"}]}}
    steps, _context, _uid, missing = render_payload(steps, context, "x", {})
    names = {m[0] for m in missing}
    paths = [m[1] for m in missing]
    assert names == {"distribution_id", "nope", "alsomissing"}
    assert "steps[1].prompt" in paths
    assert any("next.steps[0].prompt" in p for p in paths)
    # left verbatim, never blanked — the 400 body is the only consumer
    assert "{{nope}}" in steps[1]["prompt"]


def test_format_missing_dedupes_and_names_paths():
    msg = format_missing([("cdn", "steps[0].prompt"), ("cdn", "steps[0].prompt"),
                          ("cdn", "steps[1].prompt")])
    assert msg.count("{{cdn}}") == 2
    assert "steps[0].prompt" in msg and "steps[1].prompt" in msg


def test_non_string_scalars_survive_rendering():
    steps, _c, _u, _m = render_payload(
        [{"agent": "a", "prompt": "{{input}}", "timeout": 300, "output_as": ""}],
        {}, "hi", {})
    assert steps[0]["timeout"] == 300 and isinstance(steps[0]["timeout"], int)
    assert steps[0]["prompt"] == "hi"


def test_extract_artifacts_aligns_with_steps_and_strips_field():
    steps = [
        {"agent": "harness", "prompt": "a",
         "artifact": {"type": "file", "label": "GDD", "pattern": "gdd-1.md"}},
        {"agent": "opengame", "prompt": "b"},
        {"agent": "kiro", "prompt": "c", "artifact": None},
    ]
    artifacts = extract_artifacts(steps)
    assert len(artifacts) == 3
    assert artifacts[0]["pattern"] == "gdd-1.md"
    assert artifacts[1] is None and artifacts[2] is None
    assert all("artifact" not in s for s in steps)


def test_resolve_artifacts_finds_files_in_shared_cwd():
    cwd = tempfile.mkdtemp()
    with open(os.path.join(cwd, "gdd-abcd.md"), "w") as f:
        f.write("x")
    artifacts = [{"type": "file", "label": "GDD", "pattern": "gdd-abcd.md"},
                 None,
                 {"type": "url", "label": "CDN", "pattern": "https://cdn.example/g/"}]
    steps = [{"agent": "harness"}, {"agent": "opengame"}, {"agent": "kiro"}]
    out = resolve_artifacts(artifacts, steps, cwd)
    assert [e["step"] for e in out] == [0, 2]
    assert out[0]["exists"] is True
    assert out[0]["path"] == os.path.join(cwd, "gdd-abcd.md")
    assert out[0]["agent"] == "harness"
    assert out[1]["url"] == "https://cdn.example/g/"


def test_resolve_artifacts_marks_missing_file():
    artifacts = [{"type": "file", "label": "GDD", "pattern": "nope-*.md"}]
    out = resolve_artifacts(artifacts, [{"agent": "harness"}], tempfile.mkdtemp())
    assert out[0]["exists"] is False and "path" not in out[0]


# ============================================================================
# Chained artifacts (v0.38.0) — context.next.steps[].artifact
# ============================================================================

def test_extract_chained_stashes_where_auto_chain_will_inherit():
    """_auto_chain does next_def["context"].copy() -> child sees _artifacts."""
    context = {"next": {"mode": "sequence", "steps": [
        {"agent": "qwen", "prompt": "sum",
         "artifact": {"type": "file", "label": "报告", "pattern": "report.md"}},
        {"agent": "kiro", "prompt": "ppt"},
    ]}}
    found = extract_chained_artifacts(context)
    assert found == 1
    child = context["next"]["context"]["_artifacts"]
    assert child[0]["pattern"] == "report.md"
    assert child[1] is None  # aligned with the child's steps
    # stripped, so PipelineManager.submit() never sees an unknown key
    assert all("artifact" not in s for s in context["next"]["steps"])


def test_extract_chained_simulates_auto_chain_inheritance():
    """Replicates src/pipeline.py:_auto_chain context construction verbatim."""
    context = {"shared_cwd": "/tmp/opengame", "next": {"mode": "sequence", "steps": [
        {"agent": "qwen", "prompt": "sum",
         "artifact": {"type": "file", "label": "报告", "pattern": "report.md"}},
    ]}}
    extract_chained_artifacts(context)
    propagate_uid(context, "deadbeef")

    next_def = context["next"]
    next_context = next_def.get("context", {}).copy()
    next_context.setdefault("shared_cwd", context.get("shared_cwd", ""))

    assert next_context["_artifacts"][0]["pattern"] == "report.md"
    assert next_context["_uid"] == "deadbeef"
    assert next_context["shared_cwd"] == "/tmp/opengame"


def test_extract_chained_recurses_through_nested_next():
    context = {"next": {
        "mode": "sequence",
        "steps": [{"agent": "a", "prompt": "x",
                   "artifact": {"type": "file", "pattern": "a.md"}}],
        "next": {"mode": "sequence",
                 "steps": [{"agent": "b", "prompt": "y",
                            "artifact": {"type": "url", "pattern": "https://d/"}}]},
    }}
    assert extract_chained_artifacts(context) == 2
    assert context["next"]["context"]["_artifacts"][0]["pattern"] == "a.md"
    assert context["next"]["next"]["context"]["_artifacts"][0]["pattern"] == "https://d/"


def test_extract_chained_is_noop_without_next():
    context = {"shared_cwd": "/tmp/x"}
    assert extract_chained_artifacts(context) == 0
    assert context == {"shared_cwd": "/tmp/x"}


def test_extract_chained_leaves_no_context_key_when_no_artifacts():
    context = {"next": {"mode": "sequence", "steps": [{"agent": "a", "prompt": "x"}]}}
    assert extract_chained_artifacts(context) == 0
    assert "_artifacts" not in context["next"].get("context", {})


def test_chained_patterns_are_rendered_before_extraction():
    """render_payload runs first, so extracted patterns carry the real uid."""
    context = {"next": {"mode": "sequence", "steps": [
        {"agent": "light-agent", "prompt": "deploy",
         "artifact": {"type": "url", "label": "PPT",
                      "pattern": "https://cdn/reports/{{uid}}/"}},
    ]}}
    _steps, context, uid, _missing = render_payload([], context, "x", {})
    extract_chained_artifacts(context)
    assert context["next"]["context"]["_artifacts"][0]["pattern"] == \
        f"https://cdn/reports/{uid}/"


def test_propagate_uid_reaches_every_chain_level():
    context = {"next": {"mode": "sequence", "steps": [],
                        "next": {"mode": "sequence", "steps": []}}}
    propagate_uid(context, "cafe1234")
    assert context["next"]["context"]["_uid"] == "cafe1234"
    assert context["next"]["next"]["context"]["_uid"] == "cafe1234"


def test_propagate_uid_ignores_empty_uid():
    context = {"next": {"mode": "sequence", "steps": []}}
    propagate_uid(context, "")
    assert "context" not in context["next"]


# ============================================================================
# Route wiring
# ============================================================================

class _FakePipeline:
    def __init__(self, mode, steps, context, pipeline_id="pl-test"):
        self.pipeline_id = pipeline_id
        self.status = "pending"
        self.mode = mode
        self.steps = steps
        self.context = context

    def to_dict(self):
        return {"pipeline_id": self.pipeline_id, "mode": self.mode,
                "status": self.status,
                "steps": [{"agent": s["agent"], "status": "completed"}
                          for s in self.steps]}


class _FakePipelineManager:
    def __init__(self):
        self.submitted = None
        self.pipeline = None
        self._all = {}

    def submit(self, mode, steps, context=None, webhook_meta=None):
        # First submit is the parent (id pl-test, what most assertions read);
        # later ones simulate _auto_chain children.
        pid = "pl-test" if not self._all else f"pl-child-{len(self._all)}"
        if pid == "pl-test":
            self.submitted = {"mode": mode, "steps": steps, "context": context or {}}
        pl = _FakePipeline(mode, steps, context or {}, pid)
        if pid == "pl-test":
            self.pipeline = pl
        self._all[pid] = pl
        return pl

    def get(self, pipeline_id):
        return self._all.get(pipeline_id)

    def get_transcript(self, pipeline_id):
        return []


def _app():
    mgr = _FakePipelineManager()
    app = FastAPI()
    pipelines_routes.register(app, mgr)
    return app, mgr


async def _post(app, payload):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.post("/pipelines", json=payload)


@pytest.mark.asyncio
async def test_route_renders_payload_before_submit():
    app, mgr = _app()
    resp = await _post(app, {
        "mode": "sequence",
        "input": "racing game",
        "vars": {"bucket": "opengame-demo"},
        "context": {"shared_cwd": "/tmp/opengame"},
        "steps": [{"agent": "harness", "prompt": "idea: {{input}} -> gdd-{{uid}}.md"},
                  {"agent": "qwen", "prompt": "s3://{{bucket}}/{{uid}}/"}],
    })
    assert resp.status_code == 200
    uid = resp.json()["uid"]
    submitted = mgr.submitted["steps"]
    assert submitted[0]["prompt"] == f"idea: racing game -> gdd-{uid}.md"
    assert submitted[1]["prompt"] == f"s3://opengame-demo/{uid}/"
    assert mgr.submitted["context"]["_uid"] == uid


@pytest.mark.asyncio
async def test_route_rejects_unresolved_variables_and_submits_nothing():
    app, mgr = _app()
    resp = await _post(app, {
        "mode": "sequence",
        "input": "racing game",
        "steps": [{"agent": "harness", "prompt": "upload to s3://{{bucket}}/"}],
    })
    assert resp.status_code == 400
    assert "{{bucket}}" in resp.json()["error"]
    assert "steps[0].prompt" in resp.json()["error"]
    assert mgr.submitted is None  # nothing was queued


@pytest.mark.asyncio
async def test_route_is_backward_compatible_without_input_or_vars():
    """No input/vars -> no rendering at all; literal {{uid}} passes through."""
    app, mgr = _app()
    resp = await _post(app, {
        "mode": "sequence",
        "steps": [{"agent": "harness", "prompt": "literal {{uid}} stays"}],
    })
    assert resp.status_code == 200
    assert "uid" not in resp.json()
    assert mgr.submitted["steps"][0]["prompt"] == "literal {{uid}} stays"
    assert "_uid" not in mgr.submitted["context"]
    assert "_artifacts" not in mgr.submitted["context"]


@pytest.mark.asyncio
async def test_route_strips_artifact_from_steps_and_stashes_in_context():
    app, mgr = _app()
    resp = await _post(app, {
        "mode": "sequence",
        "input": "racing game",
        "steps": [
            {"agent": "harness", "prompt": "{{input}}",
             "artifact": {"type": "file", "label": "GDD", "pattern": "gdd-{{uid}}.md"}},
            {"agent": "opengame", "prompt": "build"},
        ],
    })
    assert resp.status_code == 200
    uid = resp.json()["uid"]
    # pipeline.submit() must not see the artifact key — src/pipeline.py is untouched
    assert all("artifact" not in s for s in mgr.submitted["steps"])
    artifacts = mgr.submitted["context"]["_artifacts"]
    assert artifacts[0]["pattern"] == f"gdd-{uid}.md" and artifacts[1] is None
    assert resp.json()["artifacts"][0]["label"] == "GDD"


@pytest.mark.asyncio
async def test_route_does_not_mutate_caller_context_dict():
    """context.copy() before injecting _artifacts/_uid."""
    app, mgr = _app()
    resp = await _post(app, {
        "mode": "sequence",
        "input": "x",
        "context": {"shared_cwd": "/tmp/opengame"},
        "steps": [{"agent": "a", "prompt": "{{input}}"}],
    })
    assert resp.status_code == 200
    assert mgr.submitted["context"]["shared_cwd"] == "/tmp/opengame"


@pytest.mark.asyncio
async def test_get_pipeline_resolves_artifacts_from_shared_cwd():
    app, mgr = _app()
    cwd = tempfile.mkdtemp()
    resp = await _post(app, {
        "mode": "sequence",
        "input": "racing",
        "context": {"shared_cwd": cwd},
        "steps": [{"agent": "harness", "prompt": "{{input}}",
                   "artifact": {"type": "file", "label": "GDD",
                                "pattern": "gdd-{{uid}}.md"}}],
    })
    uid = resp.json()["uid"]
    with open(os.path.join(cwd, f"gdd-{uid}.md"), "w") as f:
        f.write("done")

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        got = await client.get("/pipelines/pl-test")
    body = got.json()
    assert body["uid"] == uid
    assert body["artifacts"][0]["exists"] is True
    assert body["artifacts"][0]["path"].endswith(f"gdd-{uid}.md")


@pytest.mark.asyncio
async def test_conversation_mode_renders_topic_and_initial_context():
    app, mgr = _app()
    resp = await _post(app, {
        "mode": "conversation",
        "input": "microservices vs monolith",
        "vars": {"style": "socratic"},
        "participants": ["kiro", "claude"],
        "topic": "debate: {{input}}",
        "initial_context": "style={{style}}, run={{uid}}",
    })
    assert resp.status_code == 200
    uid = resp.json()["uid"]
    ctx = mgr.submitted["context"]
    assert ctx["topic"] == "debate: microservices vs monolith"
    assert ctx["initial_context"] == f"style=socratic, run={uid}"


# ============================================================================
# Route wiring — real agent-space artifacts.json payload shapes (v0.38.0)
# ============================================================================

@pytest.mark.asyncio
async def test_route_handles_make_game_shape_all_artifacts_top_level():
    """artifacts.json `make-game`: sequence, 3 artifacts on top-level steps."""
    app, mgr = _app()
    resp = await _post(app, {
        "mode": "sequence",
        "input": "赛车游戏",
        "context": {"shared_cwd": "/tmp/opengame"},
        "steps": [
            {"agent": "harness", "prompt": "想法：{{input}}，写 gdd.md",
             "artifact": {"type": "file", "label": "GDD", "pattern": "gdd.md"}},
            {"agent": "opengame", "prompt": "实现为 {{uid}}.html",
             "artifact": {"type": "file", "label": "游戏文件",
                          "pattern": "{{uid}}.html"}},
            {"agent": "kiro", "prompt": "部署 {{uid}}.html",
             "artifact": {"type": "url", "label": "URL",
                          "pattern": "https://d1x0y8igxbg2j0.cloudfront.net"}},
        ],
    })
    assert resp.status_code == 200
    body = resp.json()
    uid = body["uid"]
    assert len(body["artifacts"]) == 3
    assert body["artifacts"][1]["pattern"] == f"{uid}.html"
    assert "chained_artifacts" not in body
    assert mgr.submitted["context"]["_artifacts"][1]["pattern"] == f"{uid}.html"
    assert all("artifact" not in s for s in mgr.submitted["steps"])


@pytest.mark.asyncio
async def test_route_handles_stock_research_shape_artifacts_only_in_chain():
    """artifacts.json `stock-research`: parallel, all 3 artifacts in context.next."""
    app, mgr = _app()
    resp = await _post(app, {
        "mode": "parallel",
        "input": "贵州茅台",
        "context": {"next": {"mode": "sequence", "inject_upstream": "text", "steps": [
            {"agent": "qwen", "prompt": "汇总",
             "artifact": {"type": "file", "label": "报告", "pattern": "report.md"}},
            {"agent": "kiro", "prompt": "做 PPT",
             "artifact": {"type": "file", "label": "PPT", "pattern": "ppt.html"}},
            {"agent": "light-agent", "prompt": "部署 {{uid}}",
             "artifact": {"type": "url", "label": "PPT",
                          "pattern": "https://cdn/reports/{{uid}}/"}},
        ]}},
        "steps": [{"agent": "kiro-stock", "prompt": "基本面 {{input}}"},
                  {"agent": "kiro-stock", "prompt": "技术面 {{input}}"}],
    })
    assert resp.status_code == 200
    body = resp.json()
    uid = body["uid"]
    assert "artifacts" not in body           # nothing on the top-level steps
    assert body["chained_artifacts"] == 3    # but the chain declares 3
    ctx = mgr.submitted["context"]
    assert "_artifacts" not in ctx
    child = ctx["next"]["context"]["_artifacts"]
    assert [a["pattern"] for a in child] == \
        ["report.md", "ppt.html", f"https://cdn/reports/{uid}/"]
    assert child[2]["pattern"] == f"https://cdn/reports/{uid}/"
    assert ctx["next"]["context"]["_uid"] == uid
    # the child pipeline's steps must be clean for PipelineManager.submit()
    assert all("artifact" not in s for s in ctx["next"]["steps"])
    assert mgr.submitted["steps"][0]["prompt"] == "基本面 贵州茅台"


@pytest.mark.asyncio
async def test_route_handles_brainstorm_shape_conversation_plus_chain():
    """artifacts.json `brainstorm`: conversation, 1 artifact in context.next."""
    app, mgr = _app()
    resp = await _post(app, {
        "mode": "conversation",
        "input": "AI agent 的未来",
        "participants": ["kiro", "claude"],
        "topic": "{{input}}",
        "context": {"next": {"mode": "sequence", "inject_upstream": "text", "steps": [
            {"agent": "light-agent", "prompt": "总结并上传 {{uid}}.md",
             "artifact": {"type": "url", "label": "📄 总结",
                          "pattern": "https://cdn/reports/"}},
        ]}},
    })
    assert resp.status_code == 200
    body = resp.json()
    uid = body["uid"]
    assert body["chained_artifacts"] == 1
    ctx = mgr.submitted["context"]
    assert ctx["topic"] == "AI agent 的未来"
    assert ctx["next"]["context"]["_artifacts"][0]["label"] == "📄 总结"
    assert ctx["next"]["context"]["_uid"] == uid
    assert ctx["next"]["steps"][0]["prompt"] == f"总结并上传 {uid}.md"


@pytest.mark.asyncio
async def test_route_chained_artifacts_work_without_rendering():
    """Chain extraction is independent of input/vars — pre-rendered payloads too."""
    app, mgr = _app()
    resp = await _post(app, {
        "mode": "parallel",
        "steps": [{"agent": "a", "prompt": "x"}],
        "context": {"next": {"mode": "sequence", "steps": [
            {"agent": "qwen", "prompt": "sum",
             "artifact": {"type": "file", "label": "R", "pattern": "report.md"}},
        ]}},
    })
    assert resp.status_code == 200
    assert resp.json()["chained_artifacts"] == 1
    assert "uid" not in resp.json()
    ctx = mgr.submitted["context"]
    assert ctx["next"]["context"]["_artifacts"][0]["pattern"] == "report.md"
    assert "_uid" not in ctx["next"].get("context", {})


@pytest.mark.asyncio
async def test_get_child_pipeline_resolves_inherited_artifacts():
    """The child pipeline's own GET resolves what it inherited from the chain."""
    app, mgr = _app()
    cwd = tempfile.mkdtemp()
    resp = await _post(app, {
        "mode": "parallel",
        "input": "贵州茅台",
        "context": {"shared_cwd": cwd, "next": {"mode": "sequence", "steps": [
            {"agent": "qwen", "prompt": "汇总",
             "artifact": {"type": "file", "label": "报告",
                          "pattern": "report-{{uid}}.md"}},
        ]}},
        "steps": [{"agent": "kiro-stock", "prompt": "{{input}}"}],
    })
    uid = resp.json()["uid"]

    # Simulate _auto_chain building + submitting the child pipeline
    next_def = mgr.submitted["context"]["next"]
    child_ctx = next_def["context"].copy()
    child_ctx.setdefault("shared_cwd", mgr.submitted["context"]["shared_cwd"])
    child = mgr.submit(next_def["mode"], next_def["steps"], child_ctx)

    with open(os.path.join(cwd, f"report-{uid}.md"), "w") as f:
        f.write("done")

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        got = await c.get(f"/pipelines/{child.pipeline_id}")
    body = got.json()
    assert body["uid"] == uid
    assert body["artifacts"][0]["label"] == "报告"
    assert body["artifacts"][0]["exists"] is True
    assert body["artifacts"][0]["path"].endswith(f"report-{uid}.md")


# ============================================================================
# publish_artifacts (v0.39.0) — S3 enrichment for webhook delivery
# ============================================================================

from src.render import publish_artifacts


def _ws_with_artifact(tmpdir: str) -> dict:
    with open(os.path.join(tmpdir, "gdd-ab12.md"), "w") as f:
        f.write("# GDD")
    return {
        "shared_cwd": tmpdir,
        "_uid": "ab12",
        "_artifacts": [
            {"type": "file", "label": "GDD", "pattern": "gdd-*.md"},
            {"type": "file", "label": "Zip", "pattern": "nope-*.zip"},
            {"type": "url", "label": "Site", "pattern": "https://cdn.example.com/x"},
        ],
    }


STEPS3 = [{"agent": "harness"}, {"agent": "kiro"}, {"agent": "claude"}]


@pytest.mark.asyncio
async def test_publish_artifacts_uploads_existing_files(monkeypatch):
    from src import s3
    monkeypatch.setattr(s3, "is_available", lambda: True)
    uploaded = {}

    def fake_upload(path, key):
        uploaded[key] = path
        return f"https://s3.example/{key}?sig=1"

    monkeypatch.setattr(s3, "upload", fake_upload)
    with tempfile.TemporaryDirectory() as td:
        out = await publish_artifacts(_ws_with_artifact(td), STEPS3)
    assert out[0]["url"] == "https://s3.example/artifacts/ab12/gdd-ab12.md?sig=1"
    assert list(uploaded) == ["artifacts/ab12/gdd-ab12.md"]
    assert out[1]["exists"] is False and "url" not in out[1]   # missing: no upload
    assert out[2]["url"].startswith("https://cdn.example.com") # url type passthrough


@pytest.mark.asyncio
async def test_publish_artifacts_degrades_without_s3(monkeypatch):
    from src import s3
    monkeypatch.setattr(s3, "is_available", lambda: False)
    with tempfile.TemporaryDirectory() as td:
        out = await publish_artifacts(_ws_with_artifact(td), STEPS3)
    assert out[0]["exists"] is True and "url" not in out[0]
    assert out[0]["path"].endswith("gdd-ab12.md")


@pytest.mark.asyncio
async def test_publish_artifacts_never_raises_on_upload_error(monkeypatch):
    from src import s3
    monkeypatch.setattr(s3, "is_available", lambda: True)

    def boom(path, key):
        raise RuntimeError("s3 down")

    monkeypatch.setattr(s3, "upload", boom)
    with tempfile.TemporaryDirectory() as td:
        out = await publish_artifacts(_ws_with_artifact(td), STEPS3)
    assert "url" not in out[0] and out[0]["path"]


@pytest.mark.asyncio
async def test_publish_artifacts_empty_when_no_declarations():
    assert await publish_artifacts({"shared_cwd": "/tmp"}, []) == []
