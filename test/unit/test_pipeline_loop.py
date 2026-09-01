"""
Unit tests for pipeline.py convergence loop (v0.44.0):
_eval_condition (AST-whitelist safety), _read_verdict, _maybe_loop outcomes
(converge / loop-back / max_rounds / fail-safe).
"""

import json
from unittest.mock import AsyncMock, Mock, patch

import pytest

from src.acp_client import AcpProcessPool
from src.pipeline import Pipeline, PipelineManager, PipelineStep


@pytest.fixture
def mock_pool():
    pool = Mock(spec=AcpProcessPool)
    pool._connections = {}
    pool._config = {}
    return pool


@pytest.fixture
def manager(mock_pool, tmp_path):
    return PipelineManager(
        pool=mock_pool,
        agents_cfg={"kiro": {}, "opengame": {}, "qa-agent": {}},
        db_path=str(tmp_path / "test.db"),
    )


def _pl(steps, context):
    return Pipeline(
        pipeline_id="0123456789abcdef",
        mode="sequence",
        steps=[PipelineStep(agent=s["agent"], prompt_template=s["prompt"],
                            output_as=s.get("output_as", "")) for s in steps],
        status="completed",
        context=context,
    )


# ============================================================================
# _eval_condition — safe AST-whitelist evaluator
# ============================================================================

class TestEvalCondition:
    NS = {"verdict": {"overall": 72, "gameplay": 90}, "round": 2}

    @pytest.mark.parametrize("expr,want", [
        ("verdict.overall < 80", True),
        ("verdict.overall >= 80", False),
        ("verdict.overall < 80 and verdict.gameplay > 85", True),
        ("verdict.overall < 80 or verdict.gameplay < 50", True),
        ("not verdict.overall >= 80", True),
        ("round == 2", True),
        ("round != 2", False),
        ("verdict.missing < 80", False),   # missing metric -> False (fail-safe)
    ])
    def test_valid_expressions(self, expr, want):
        assert PipelineManager._eval_condition(expr, self.NS) is want

    @pytest.mark.parametrize("expr", [
        "__import__('os').system('id')",
        "verdict.__class__",
        "open('/etc/passwd')",
        "verdict['overall']",
        "verdict.overall.bit_length()",
        "1 if True else 2",
        "[x for x in range(3)]",
    ])
    def test_rejects_injection(self, expr):
        with pytest.raises(ValueError):
            PipelineManager._eval_condition(expr, self.NS)


# ============================================================================
# _read_verdict — load machine-readable verdict from shared_cwd
# ============================================================================

class TestReadVerdict:
    def test_reads_and_namespaces(self, manager, tmp_path):
        (tmp_path / "verdict.json").write_text(json.dumps({"overall": 75}))
        pl = _pl([{"agent": "qa-agent", "prompt": "x"}],
                 {"shared_cwd": str(tmp_path), "_loop_round": 1})
        ns = manager._read_verdict(pl, "verdict.json")
        assert ns == {"verdict": {"overall": 75}, "round": 1}

    def test_missing_file_returns_none(self, manager, tmp_path):
        pl = _pl([{"agent": "qa-agent", "prompt": "x"}], {"shared_cwd": str(tmp_path)})
        assert manager._read_verdict(pl, "verdict.json") is None

    def test_bad_json_returns_none(self, manager, tmp_path):
        (tmp_path / "verdict.json").write_text("{not json")
        pl = _pl([{"agent": "qa-agent", "prompt": "x"}], {"shared_cwd": str(tmp_path)})
        assert manager._read_verdict(pl, "verdict.json") is None

    def test_no_shared_cwd_returns_none(self, manager):
        pl = _pl([{"agent": "qa-agent", "prompt": "x"}], {})
        assert manager._read_verdict(pl, "verdict.json") is None


# ============================================================================
# _maybe_loop — converge / loop-back / max_rounds / fail-safe
# ============================================================================

@pytest.mark.asyncio
class TestMaybeLoop:
    def _steps(self):
        return [{"agent": "kiro", "prompt": "scaffold"},
                {"agent": "opengame", "prompt": "fix round {_loop_round}"},
                {"agent": "qa-agent", "prompt": "qa"}]

    def _loop_ctx(self, tmp_path, overall, extra=None):
        (tmp_path / "verdict.json").write_text(json.dumps({"overall": overall}))
        ctx = {
            "shared_cwd": str(tmp_path),
            "next": {"when": "verdict.overall < 80", "loop_back_to": 1,
                     "max_rounds": 3, "when_source": "verdict.json"},
        }
        if extra:
            ctx.update(extra)
        return ctx

    async def test_converges_no_resubmit(self, manager, tmp_path):
        pl = _pl(self._steps(), self._loop_ctx(tmp_path, overall=85))
        manager.submit = Mock()
        manager._loop_webhook = AsyncMock()
        await manager._maybe_loop(pl)
        manager.submit.assert_not_called()
        assert "converged" in manager._loop_webhook.call_args[0][2]

    async def test_loops_back_resubmits_tail(self, manager, tmp_path):
        pl = _pl(self._steps(), self._loop_ctx(tmp_path, overall=60))
        # positional artifacts aligned with all 3 steps
        pl.context["_artifacts"] = [{"a": 0}, {"a": 1}, {"a": 2}]
        manager.submit = Mock(return_value=Mock(pipeline_id="newpipe"))
        manager._loop_webhook = AsyncMock()
        with patch("src.pipeline.asyncio.to_thread", new=AsyncMock()):
            await manager._maybe_loop(pl)
        manager.submit.assert_called_once()
        kw = manager.submit.call_args.kwargs
        # tail from loop_back_to=1 -> 2 steps
        assert len(kw["steps"]) == 2
        assert kw["steps"][0]["agent"] == "opengame"
        assert kw["context"]["_loop_round"] == 1
        # artifacts sliced to match the tail
        assert kw["context"]["_artifacts"] == [{"a": 1}, {"a": 2}]
        assert pl.context["next_pipeline_id"] == "newpipe"
        # child IS the tail -> its loop_back_to rewritten to 0 so every further
        # round re-runs the whole tail (not a shrinking suffix), and the parent's
        # next-def is untouched (no shared-dict mutation).
        assert kw["context"]["next"]["loop_back_to"] == 0
        assert pl.context["next"]["loop_back_to"] == 1

    async def test_multi_round_tail_stays_stable(self, manager, tmp_path):
        """Round N's resubmitted tail, fed back through _maybe_loop, re-runs the
        SAME steps — the fix step must not fall out after the first round."""
        # Simulate the round-1 pipeline: it already IS the tail [fix, qa] with
        # loop_back_to rewritten to 0, round=1.
        (tmp_path / "verdict.json").write_text(json.dumps({"overall": 60}))
        ctx = {"shared_cwd": str(tmp_path), "_loop_round": 1,
               "next": {"when": "verdict.overall < 80", "loop_back_to": 0,
                        "max_rounds": 3, "when_source": "verdict.json"}}
        pl = _pl([{"agent": "opengame", "prompt": "fix"},
                  {"agent": "qa-agent", "prompt": "qa"}], ctx)
        manager.submit = Mock(return_value=Mock(pipeline_id="round2"))
        manager._loop_webhook = AsyncMock()
        with patch("src.pipeline.asyncio.to_thread", new=AsyncMock()):
            await manager._maybe_loop(pl)
        kw = manager.submit.call_args.kwargs
        assert [s["agent"] for s in kw["steps"]] == ["opengame", "qa-agent"]
        assert kw["context"]["_loop_round"] == 2
        assert kw["context"]["next"]["loop_back_to"] == 0

    async def test_stops_at_max_rounds(self, manager, tmp_path):
        # already on the last allowed round -> must not resubmit
        pl = _pl(self._steps(),
                 self._loop_ctx(tmp_path, overall=60, extra={"_loop_round": 2}))
        manager.submit = Mock()
        manager._loop_webhook = AsyncMock()
        await manager._maybe_loop(pl)
        manager.submit.assert_not_called()
        assert "max_rounds" in manager._loop_webhook.call_args[0][2]

    async def test_missing_verdict_stops(self, manager, tmp_path):
        ctx = {"shared_cwd": str(tmp_path),
               "next": {"when": "verdict.overall < 80", "loop_back_to": 1,
                        "max_rounds": 3}}
        pl = _pl(self._steps(), ctx)  # no verdict.json on disk
        manager.submit = Mock()
        manager._loop_webhook = AsyncMock()
        await manager._maybe_loop(pl)
        manager.submit.assert_not_called()
        assert "verdict" in manager._loop_webhook.call_args[0][2]

    async def test_bad_loop_back_to_stops(self, manager, tmp_path):
        ctx = self._loop_ctx(tmp_path, overall=60)
        ctx["next"]["loop_back_to"] = 9  # out of range
        pl = _pl(self._steps(), ctx)
        manager.submit = Mock()
        manager._loop_webhook = AsyncMock()
        await manager._maybe_loop(pl)
        manager.submit.assert_not_called()
        assert "越界" in manager._loop_webhook.call_args[0][2]


# ============================================================================
# Template support — loop config survives render_payload; runtime fills round
# ============================================================================

class TestLoopTemplateSupport:
    def test_template_render_preserves_loop_config(self):
        """A pipeline template with {{placeholders}} in the loop def renders the
        threshold/max_rounds while keeping the loop fields structurally intact."""
        from src.render import render_payload
        steps = [{"agent": "qa-agent", "prompt": "qa {{game}}"}]
        context = {"next": {"when": "verdict.overall < {{threshold}}",
                            "loop_back_to": 1, "max_rounds": "{{rounds}}",
                            "when_source": "verdict.json"}}
        steps, ctx, _uid, missing = render_payload(
            steps, context, variables={"game": "snake", "threshold": "80", "rounds": "3"})
        assert missing == []
        assert ctx["next"]["when"] == "verdict.overall < 80"
        assert ctx["next"]["max_rounds"] == "3"     # int() coerced later in _maybe_loop
        assert ctx["next"]["loop_back_to"] == 1

    def test_template_render_leaves_runtime_round_verbatim(self):
        """{{_loop_round}} is a runtime var — template-submit must NOT report it
        missing (which would 400 the submit) and must leave it verbatim."""
        from src.render import render_payload
        steps = [{"agent": "opengame", "prompt": "read round {{_loop_round}}, fix {{game}}"}]
        steps, _ctx, _uid, missing = render_payload(steps, {}, variables={"game": "snake"})
        assert missing == []
        assert steps[0]["prompt"] == "read round {{_loop_round}}, fix snake"

    def test_runtime_render_fills_round(self):
        """Runtime _render fills {{_loop_round}} from context each round."""
        out = PipelineManager._render("round {{_loop_round}}", {"_loop_round": 2})
        assert out == "round 2"
