"""
Unit tests for v0.19.0 composable pipeline features:
- shared_cwd inheritance
- output_schema extraction
- pause/resume/inject
- artifacts endpoint
"""

import asyncio
import json
import os
import time

import pytest
from unittest.mock import AsyncMock, Mock, patch

from src.acp_client import AcpConnection, AcpProcessPool
from src.pipeline import Pipeline, PipelineStep, PipelineManager


# ============================================================================
# Fixtures
# ============================================================================

@pytest.fixture
def agents_cfg():
    return {
        "kiro": {"command": "echo", "working_dir": "/tmp", "description": "kiro agent", "mode": "acp"},
        "claude": {"command": "echo", "working_dir": "/tmp", "description": "claude agent", "mode": "acp"},
    }


@pytest.fixture
def manager(agents_cfg, tmp_path):
    pool = Mock(spec=AcpProcessPool)
    pool._connections = {}
    return PipelineManager(pool=pool, agents_cfg=agents_cfg, db_path=str(tmp_path / "test.db"))


def _conn(text="ok"):
    conn = AsyncMock(spec=AcpConnection)
    async def prompt(p, idle_timeout=300):
        yield {"method": "x", "params": {"type": "message.part", "content": text}}
        yield {"_prompt_result": {"result": {"stopReason": "end"}}}
    conn.session_prompt = prompt
    return conn


_TN = patch("src.pipeline.transform_notification", side_effect=lambda n: (
    {"type": "message.part", "content": n["params"]["content"]}
    if "params" in n and "type" in n.get("params", {}) else None
))
_PS = patch("src.pipeline.get_prompt_suffix", return_value="")
_LP = patch("src.pipeline._load_prompt", return_value="Topic: {topic}\nAgent: {agent}\n{participants}\n{shared_cwd}")


# ============================================================================
# Test: shared_cwd inheritance
# ============================================================================

class TestSharedCwdInheritance:
    """shared_cwd passed in context is reused, not overwritten."""

    def test_reuse_existing_shared_cwd(self, manager, tmp_path):
        """If context has a valid shared_cwd dir, _make_shared_cwd reuses it."""
        existing = str(tmp_path / "inherited-workspace")
        os.makedirs(existing)
        pl = Pipeline(pipeline_id="inh-1", mode="parallel", steps=[],
                      context={"shared_cwd": existing})
        result = manager._make_shared_cwd(pl)
        assert result == existing

    def test_creates_new_if_no_shared_cwd(self, manager, tmp_path):
        """Without shared_cwd in context, creates a new directory."""
        pl = Pipeline(pipeline_id="inh-2", mode="sequence", steps=[], context={})
        result = manager._make_shared_cwd(pl)
        assert os.path.isdir(result)
        assert "inh-2" in result

    def test_creates_new_if_shared_cwd_invalid(self, manager):
        """If shared_cwd path doesn't exist, creates a new one."""
        pl = Pipeline(pipeline_id="inh-3", mode="sequence", steps=[],
                      context={"shared_cwd": "/nonexistent/path/xyz"})
        result = manager._make_shared_cwd(pl)
        assert os.path.isdir(result)
        assert result != "/nonexistent/path/xyz"

    @pytest.mark.asyncio
    async def test_cross_pipeline_inheritance(self, manager, tmp_path):
        """Second pipeline inherits first pipeline's shared_cwd."""
        pool = manager._pool
        pool.get_or_create = AsyncMock(return_value=_conn("done"))

        # First pipeline creates a workspace
        pl1 = Pipeline(pipeline_id="cross-1", mode="sequence", steps=[
            PipelineStep(agent="kiro", prompt_template="init"),
        ], context={})

        with _TN, _PS:
            with patch.object(manager, '_webhook_start', new_callable=AsyncMock):
                with patch.object(manager, '_webhook', new_callable=AsyncMock):
                    with patch.object(manager, '_webhook_step', new_callable=AsyncMock):
                        await manager._run(pl1)

        shared_cwd = pl1.context["shared_cwd"]
        assert os.path.isdir(shared_cwd)

        # Second pipeline reuses it
        pl2 = Pipeline(pipeline_id="cross-2", mode="sequence", steps=[
            PipelineStep(agent="claude", prompt_template="continue"),
        ], context={"shared_cwd": shared_cwd})

        with _TN, _PS:
            with patch.object(manager, '_webhook_start', new_callable=AsyncMock):
                with patch.object(manager, '_webhook', new_callable=AsyncMock):
                    with patch.object(manager, '_webhook_step', new_callable=AsyncMock):
                        await manager._run(pl2)

        assert pl2.context["shared_cwd"] == shared_cwd


# ============================================================================
# Test: output_schema extraction
# ============================================================================

class TestOutputExtraction:
    """Conversation output extraction from final turn."""

    @pytest.mark.asyncio
    async def test_extracts_json_block(self, manager):
        """Extracts JSON from ```json code block in last turn."""
        pool = manager._pool
        output_json = json.dumps({"tasks": [{"agent": "kiro", "role": "frontend"}]})
        response = f"Here's the plan:\n\n```json\n{output_json}\n```\n\nSTATUS: DONE"
        pool.get_or_create = AsyncMock(return_value=_conn(response))

        pl = Pipeline(pipeline_id="ext-1", mode="conversation", steps=[], context={
            "participants": ["kiro", "claude"],
            "topic": "game design",
            "config": {"max_turns": 2, "stop_conditions": ["DONE"],
                       "output_schema": {"type": "object"}, "a2a_rules": False},
        })

        with _TN, _PS, _LP:
            with patch.object(manager, '_make_shared_cwd', return_value="/tmp/ws"):
                with patch.object(manager, '_webhook_start', new_callable=AsyncMock):
                    with patch.object(manager, '_webhook', new_callable=AsyncMock):
                        with patch.object(manager, '_webhook_conversation_turn', new_callable=AsyncMock):
                            await manager._run(pl)

        assert pl.context.get("output") == {"tasks": [{"agent": "kiro", "role": "frontend"}]}

    @pytest.mark.asyncio
    async def test_extracts_inline_json(self, manager):
        """Extracts inline JSON object when no code block."""
        pool = manager._pool
        response = 'The result is {"role": "backend", "agent": "claude"} and STATUS: DONE'
        pool.get_or_create = AsyncMock(return_value=_conn(response))

        pl = Pipeline(pipeline_id="ext-2", mode="conversation", steps=[], context={
            "participants": ["kiro", "claude"],
            "topic": "test",
            "config": {"max_turns": 2, "stop_conditions": ["DONE"],
                       "output_schema": True, "a2a_rules": False},
        })

        with _TN, _PS, _LP:
            with patch.object(manager, '_make_shared_cwd', return_value="/tmp/ws"):
                with patch.object(manager, '_webhook_start', new_callable=AsyncMock):
                    with patch.object(manager, '_webhook', new_callable=AsyncMock):
                        with patch.object(manager, '_webhook_conversation_turn', new_callable=AsyncMock):
                            await manager._run(pl)

        assert pl.context["output"]["role"] == "backend"

    @pytest.mark.asyncio
    async def test_no_extraction_without_schema(self, manager):
        """No output extraction when output_schema is not set."""
        pool = manager._pool
        response = '{"data": "test"} STATUS: DONE'
        pool.get_or_create = AsyncMock(return_value=_conn(response))

        pl = Pipeline(pipeline_id="ext-3", mode="conversation", steps=[], context={
            "participants": ["kiro", "claude"],
            "topic": "test",
            "config": {"max_turns": 2, "stop_conditions": ["DONE"], "a2a_rules": False},
        })

        with _TN, _PS, _LP:
            with patch.object(manager, '_make_shared_cwd', return_value="/tmp/ws"):
                with patch.object(manager, '_webhook_start', new_callable=AsyncMock):
                    with patch.object(manager, '_webhook', new_callable=AsyncMock):
                        with patch.object(manager, '_webhook_conversation_turn', new_callable=AsyncMock):
                            await manager._run(pl)

        assert "output" not in pl.context

    @pytest.mark.asyncio
    async def test_output_in_to_dict(self, manager):
        """Extracted output appears in to_dict()."""
        pl = Pipeline(pipeline_id="ext-4", mode="conversation", steps=[], context={
            "participants": ["kiro", "claude"], "topic": "t",
            "output": {"tasks": [{"agent": "kiro"}]},
        })
        d = pl.to_dict()
        assert d["output"] == {"tasks": [{"agent": "kiro"}]}


# ============================================================================
# Test: pause/resume/inject
# ============================================================================

class TestPauseResumeInject:
    """Human-in-the-loop: pause, resume, inject message."""

    @pytest.mark.asyncio
    async def test_pause_and_resume(self, manager):
        """Pipeline pauses and resumes correctly."""
        pool = manager._pool
        turn_count = 0

        async def counting_conn(agent, sid, cwd=""):
            nonlocal turn_count
            turn_count += 1
            return _conn(f"turn {turn_count}")

        pool.get_or_create = AsyncMock(side_effect=counting_conn)

        pl = Pipeline(pipeline_id="pr-1", mode="conversation", steps=[], context={
            "participants": ["kiro", "claude"],
            "topic": "test pause",
            "config": {"max_turns": 4, "stop_conditions": [], "a2a_rules": False},
        })

        async def pause_after_2_turns():
            """Pause after 2 turns, wait, then resume."""
            while turn_count < 2:
                await asyncio.sleep(0.01)
            pl._gate.clear()
            await asyncio.sleep(0.1)
            # Verify it's paused
            paused_at = turn_count
            await asyncio.sleep(0.05)
            assert turn_count == paused_at, "Should not advance while paused"
            # Resume
            pl._gate.set()

        with _TN, _PS, _LP:
            with patch.object(manager, '_make_shared_cwd', return_value="/tmp/ws"):
                with patch.object(manager, '_webhook_start', new_callable=AsyncMock):
                    with patch.object(manager, '_webhook', new_callable=AsyncMock):
                        with patch.object(manager, '_webhook_conversation_turn', new_callable=AsyncMock):
                            task = asyncio.create_task(manager._run(pl))
                            await pause_after_2_turns()
                            await task

        assert pl.status == "completed"
        assert pl.context["turns"] == 4

    @pytest.mark.asyncio
    async def test_inject_message(self, manager):
        """Injected message appears in transcript as Human turn."""
        pool = manager._pool
        pool.get_or_create = AsyncMock(return_value=_conn("acknowledged"))

        pl = Pipeline(pipeline_id="inj-1", mode="conversation", steps=[], context={
            "participants": ["kiro", "claude"],
            "topic": "test inject",
            "config": {"max_turns": 4, "stop_conditions": ["DONE"], "a2a_rules": False},
        })

        # Pre-load inject queue before running
        await pl._inject_queue.put("Use Phaser.js framework")

        # Make second turn return DONE
        call_count = 0
        async def conn_with_done(agent, sid, cwd=""):
            nonlocal call_count
            call_count += 1
            if call_count >= 2:
                return _conn("Got it. STATUS: DONE")
            return _conn("thinking...")

        pool.get_or_create = AsyncMock(side_effect=conn_with_done)

        with _TN, _PS, _LP:
            with patch.object(manager, '_make_shared_cwd', return_value="/tmp/ws"):
                with patch.object(manager, '_webhook_start', new_callable=AsyncMock):
                    with patch.object(manager, '_webhook', new_callable=AsyncMock):
                        with patch.object(manager, '_webhook_conversation_turn', new_callable=AsyncMock):
                            await manager._run(pl)

        # First turn should be Human inject
        transcript = pl.context["transcript"]
        assert transcript[0]["agent"] == "Human"
        assert "Phaser.js" in transcript[0]["content"]

    @pytest.mark.asyncio
    async def test_inject_auto_resumes(self, manager):
        """Injecting a message while paused auto-resumes the pipeline."""
        pl = Pipeline(pipeline_id="inj-2", mode="conversation", steps=[], context={
            "participants": ["kiro", "claude"],
            "topic": "test",
            "config": {"max_turns": 2, "stop_conditions": [], "a2a_rules": False},
        })
        pl._gate.clear()  # paused

        # Simulate what the route handler does
        await pl._inject_queue.put("resume with this")
        pl._gate.set()

        assert pl._gate.is_set()

    def test_paused_field_in_to_dict(self):
        """to_dict includes paused status."""
        pl = Pipeline(pipeline_id="p-1", mode="conversation", steps=[], context={
            "participants": ["a", "b"], "topic": "t"})
        assert pl.to_dict()["paused"] is False
        pl._gate.clear()
        assert pl.to_dict()["paused"] is True


# ============================================================================
# Test: artifacts endpoint logic
# ============================================================================

class TestArtifacts:
    """List files in shared_cwd."""

    def test_list_files_in_workspace(self, tmp_path):
        """Artifacts lists all non-hidden files recursively."""
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        (workspace / "game.js").write_text("// game")
        (workspace / "assets").mkdir()
        (workspace / "assets" / "sprite.png").write_bytes(b"\x89PNG")
        (workspace / ".hidden").write_text("secret")

        # Simulate what the route does
        files = []
        for root, dirs, filenames in os.walk(str(workspace)):
            dirs[:] = [d for d in dirs if not d.startswith(".")]
            for f in filenames:
                if f.startswith("."):
                    continue
                full = os.path.join(root, f)
                rel = os.path.relpath(full, str(workspace))
                files.append({"path": rel, "size": os.path.getsize(full)})

        assert len(files) == 2
        paths = {f["path"] for f in files}
        assert "game.js" in paths
        assert os.path.join("assets", "sprite.png") in paths

    def test_empty_workspace(self, tmp_path):
        """Empty workspace returns empty file list."""
        workspace = tmp_path / "empty"
        workspace.mkdir()

        files = []
        for root, dirs, filenames in os.walk(str(workspace)):
            for f in filenames:
                full = os.path.join(root, f)
                rel = os.path.relpath(full, str(workspace))
                files.append({"path": rel, "size": os.path.getsize(full)})

        assert files == []


# ============================================================================
# Test: auto-chain (next)
# ============================================================================

class TestAutoChain:
    """Pipeline auto-chains to next when `next` is in context."""

    @pytest.mark.asyncio
    async def test_auto_chain_submits_next(self, manager):
        """Completed pipeline with `next` auto-submits the next pipeline."""
        pool = manager._pool
        pool.get_or_create = AsyncMock(return_value=_conn("done"))

        pl = Pipeline(pipeline_id="chain-1", mode="sequence", steps=[
            PipelineStep(agent="kiro", prompt_template="step1"),
        ], context={
            "next": {
                "mode": "parallel",
                "steps": [
                    {"agent": "kiro", "prompt": "build frontend"},
                    {"agent": "claude", "prompt": "build backend"},
                ],
            }
        })

        def fake_make_cwd(p):
            p.context["shared_cwd"] = "/tmp/ws"
            return "/tmp/ws"

        with _TN, _PS:
            with patch.object(manager, '_make_shared_cwd', side_effect=fake_make_cwd):
                with patch.object(manager, '_webhook_start', new_callable=AsyncMock):
                    with patch.object(manager, '_webhook', new_callable=AsyncMock):
                        with patch.object(manager, '_webhook_step', new_callable=AsyncMock):
                            await manager._run(pl)

        assert pl.status == "completed"
        assert "next_pipeline_id" in pl.context
        next_pl = manager.get(pl.context["next_pipeline_id"])
        assert next_pl is not None
        assert next_pl.mode == "parallel"
        assert next_pl.context["shared_cwd"] == "/tmp/ws"

    @pytest.mark.asyncio
    async def test_auto_chain_from_output(self, manager):
        """Auto-chain with steps_from_output generates steps from conversation output."""
        pool = manager._pool
        pool.get_or_create = AsyncMock(return_value=_conn('{"tasks":[{"agent":"kiro","module":"frontend","files":["app.js"]},{"agent":"claude","module":"backend","files":["server.js"]}]} STATUS: DONE'))

        pl = Pipeline(pipeline_id="chain-2", mode="conversation", steps=[], context={
            "participants": ["kiro", "claude"],
            "topic": "plan",
            "config": {"max_turns": 2, "stop_conditions": ["DONE"],
                       "output_schema": True, "a2a_rules": False},
            "next": {
                "mode": "parallel",
                "steps_from_output": True,
                "step_prompt_template": "在 {shared_cwd} 中实现 {module}，文件: {files}",
            }
        })

        with _TN, _PS, _LP:
            with patch.object(manager, '_make_shared_cwd', return_value="/tmp/ws"):
                with patch.object(manager, '_webhook_start', new_callable=AsyncMock):
                    with patch.object(manager, '_webhook', new_callable=AsyncMock):
                        with patch.object(manager, '_webhook_conversation_turn', new_callable=AsyncMock):
                            await manager._run(pl)

        assert pl.status == "completed"
        assert "next_pipeline_id" in pl.context
        next_pl = manager.get(pl.context["next_pipeline_id"])
        assert next_pl.mode == "parallel"
        assert len(next_pl.steps) == 2
        assert next_pl.steps[0].agent == "kiro"
        assert next_pl.steps[1].agent == "claude"

    @pytest.mark.asyncio
    async def test_no_chain_on_failure(self, manager):
        """Failed pipeline does not auto-chain."""
        pool = manager._pool
        pool.get_or_create = AsyncMock(side_effect=Exception("boom"))

        pl = Pipeline(pipeline_id="chain-3", mode="sequence", steps=[
            PipelineStep(agent="kiro", prompt_template="x"),
        ], context={
            "next": {"mode": "parallel", "steps": [{"agent": "kiro", "prompt": "y"}]}
        })

        with _TN, _PS:
            with patch.object(manager, '_make_shared_cwd', return_value="/tmp/ws"):
                with patch.object(manager, '_webhook_start', new_callable=AsyncMock):
                    with patch.object(manager, '_webhook', new_callable=AsyncMock):
                        with patch.object(manager, '_webhook_step', new_callable=AsyncMock):
                            await manager._run(pl)

        assert pl.status == "failed"
        assert "next_pipeline_id" not in pl.context


# ============================================================================
# Upstream injection (parallel-then-judge): inject upstream results downstream
# ============================================================================

class TestInjectUpstream:
    def _upstream_pl(self):
        pl = Pipeline(pipeline_id="up-1", mode="parallel", steps=[
            PipelineStep(agent="kiro-stock", prompt_template="p0"),
            PipelineStep(agent="kiro-stock", prompt_template="p1"),
            PipelineStep(agent="kiro-stock", prompt_template="p2"),
        ])
        pl.steps[0].status = "completed"; pl.steps[0].result = "fundamentals: long"
        pl.steps[1].status = "completed"; pl.steps[1].result = "technicals: oversold"
        pl.steps[2].status = "failed"; pl.steps[2].result = ""  # excluded
        return pl

    def test_inject_text_prepends_completed_results(self, manager):
        pl = self._upstream_pl()
        steps = [{"agent": "kiro", "prompt": "SUMMARIZE"}]
        manager._inject_upstream_text(pl, steps)
        p = steps[0]["prompt"]
        assert "fundamentals: long" in p
        assert "technicals: oversold" in p
        assert p.rstrip().endswith("SUMMARIZE")
        assert "step 2" not in p.lower()

    def test_inject_text_noop_when_nothing_completed(self, manager):
        pl = Pipeline(pipeline_id="up-2", mode="parallel",
                      steps=[PipelineStep(agent="kiro-stock", prompt_template="p0")])
        steps = [{"agent": "kiro", "prompt": "SUMMARIZE"}]
        manager._inject_upstream_text(pl, steps)
        assert steps[0]["prompt"] == "SUMMARIZE"

    def test_inject_s3_uses_presigned_urls(self, manager):
        pl = self._upstream_pl()
        steps = [{"agent": "kiro", "prompt": "SUMMARIZE"}]
        with patch("src.s3.is_available", return_value=True), \
             patch("src.s3.upload_bytes", side_effect=lambda k, d: f"https://s3.example/{k}?sig=x"):
            manager._inject_upstream_s3(pl, steps)
        p = steps[0]["prompt"]
        assert "https://s3.example/" in p
        assert p.rstrip().endswith("SUMMARIZE")

    def test_inject_s3_falls_back_to_text_when_unavailable(self, manager):
        pl = self._upstream_pl()
        steps = [{"agent": "kiro", "prompt": "SUMMARIZE"}]
        with patch("src.s3.is_available", return_value=False):
            manager._inject_upstream_s3(pl, steps)
        p = steps[0]["prompt"]
        assert "fundamentals: long" in p
        assert "https://" not in p
