"""
Unit tests for pipeline.py — PipelineManager: submit, sequence, parallel, race, conversation.

Framework by Kiro, P0 tests lock down existing behavior before refactoring.
"""

import asyncio
import time

import pytest
from unittest.mock import AsyncMock, Mock, patch, MagicMock

from src.acp_client import AcpConnection, AcpProcessPool, AcpError, PoolExhaustedError
from src.pipeline import Pipeline, PipelineStep, PipelineManager, _VAR_RE


# ============================================================================
# Fixtures
# ============================================================================

@pytest.fixture
def mock_pool():
    """AcpProcessPool mock — get_or_create returns a mock AcpConnection."""
    pool = Mock(spec=AcpProcessPool)
    pool._connections = {}
    pool._config = {}
    return pool


@pytest.fixture
def agents_cfg():
    """Minimal agent config for 3 agents."""
    return {
        "kiro": {"command": "echo", "working_dir": "/tmp", "description": "kiro agent"},
        "claude": {"command": "echo", "working_dir": "/tmp", "description": "claude agent"},
        "qwen": {"command": "echo", "working_dir": "/tmp", "description": "qwen agent"},
    }


@pytest.fixture
def manager(mock_pool, agents_cfg, tmp_path):
    """PipelineManager with mocked pool, no webhook, tmp db."""
    return PipelineManager(
        pool=mock_pool,
        agents_cfg=agents_cfg,
        db_path=str(tmp_path / "test.db"),
    )


def _make_mock_conn(responses: list[dict] | None = None):
    """Build a mock AcpConnection whose session_prompt yields given notifications."""
    conn = AsyncMock(spec=AcpConnection)
    if responses is None:
        responses = [
            {"method": "session/event", "params": {"type": "message.part", "content": "hello"}},
            {"_prompt_result": {"result": {"stopReason": "end"}}},
        ]

    async def fake_prompt(prompt, idle_timeout=300):
        for r in responses:
            yield r

    conn.session_prompt = fake_prompt
    return conn


# ============================================================================
# Test Class: PipelineStep & Pipeline Data Structures
# ============================================================================

class TestPipelineDataStructures:
    """Tests for PipelineStep/Pipeline construction and serialization."""

    def test_step_default_state(self):
        """New step starts as pending with empty result/error."""
        step = PipelineStep(agent="kiro", prompt_template="do something")
        assert step.status == "pending"
        assert step.result == ""
        assert step.error == ""

    def test_step_to_dict_minimal(self):
        """to_dict includes agent and status at minimum."""
        step = PipelineStep(agent="kiro", prompt_template="x")
        d = step.to_dict()
        assert d == {"agent": "kiro", "status": "pending"}

    def test_step_to_dict_with_result(self):
        """to_dict includes result and duration when present."""
        step = PipelineStep(agent="kiro", prompt_template="x",
                            status="completed", result="done",
                            started_at=100.0, completed_at=105.5)
        d = step.to_dict()
        assert d["result"] == "done"
        assert d["duration"] == 5.5

    def test_pipeline_default_state(self):
        """New pipeline starts as pending."""
        pl = Pipeline(pipeline_id="test-1", mode="sequence", steps=[])
        assert pl.status == "pending"
        assert pl.error == ""

    def test_pipeline_to_dict_includes_shared_cwd(self):
        """to_dict always includes shared_cwd from context."""
        pl = Pipeline(pipeline_id="test-1", mode="sequence", steps=[],
                      context={"shared_cwd": "/tmp/ws"})
        d = pl.to_dict()
        assert d["shared_cwd"] == "/tmp/ws"

    def test_pipeline_to_dict_conversation_fields(self):
        """Conversation mode includes participants, topic, turns."""
        pl = Pipeline(pipeline_id="c1", mode="conversation", steps=[],
                      context={"participants": ["kiro", "claude"],
                               "topic": "test", "turns": 3,
                               "stop_reason": "DONE"})
        d = pl.to_dict()
        assert d["participants"] == ["kiro", "claude"]
        assert d["topic"] == "test"
        assert d["turns"] == 3
        assert d["stop_reason"] == "DONE"


# ============================================================================
# Test Class: Template Rendering
# ============================================================================

class TestTemplateRendering:
    """Tests for _render() — {{var}} substitution."""

    def test_render_simple_substitution(self, manager):
        """{{var}} replaced by context value."""
        result = manager._render("Hello {{name}}", {"name": "world"})
        assert result == "Hello world"

    def test_render_missing_var_preserved(self, manager):
        """Missing vars left as-is (not KeyError)."""
        result = manager._render("Hello {{missing}}", {})
        assert result == "Hello {{missing}}"

    def test_render_multiple_vars(self, manager):
        """Multiple vars in one template."""
        result = manager._render("{{a}} + {{b}}", {"a": "1", "b": "2"})
        assert result == "1 + 2"

    def test_render_no_vars(self, manager):
        """Plain text without vars passes through."""
        result = manager._render("no vars here", {"x": "y"})
        assert result == "no vars here"


# ============================================================================
# Test Class: Submit & Lifecycle
# ============================================================================

class TestSubmitAndLifecycle:
    """Tests for submit(), get(), list_all() — pipeline lifecycle management."""

    def test_submit_creates_pipeline(self, manager):
        """submit() returns a Pipeline with correct mode and step count."""
        with patch.object(manager, '_run', new_callable=AsyncMock):
            with patch('src.pipeline.asyncio.create_task', side_effect=lambda coro: coro.close()):
                pl = manager.submit("sequence", [
                    {"agent": "kiro", "prompt": "step1"},
                    {"agent": "claude", "prompt": "step2"},
                ])
        assert pl.mode == "sequence"
        assert len(pl.steps) == 2
        assert pl.steps[0].agent == "kiro"
        assert pl.steps[1].agent == "claude"

    def test_submit_stores_pipeline(self, manager):
        """submit() makes pipeline retrievable via get()."""
        with patch.object(manager, '_run', new_callable=AsyncMock):
            with patch('src.pipeline.asyncio.create_task', side_effect=lambda coro: coro.close()):
                pl = manager.submit("sequence", [{"agent": "kiro", "prompt": "x"}])
        assert manager.get(pl.pipeline_id) is not None

    def test_get_nonexistent_returns_none(self, manager):
        """get() returns None for unknown pipeline_id."""
        assert manager.get("nonexistent-id") is None

    def test_submit_respects_timeout(self, manager):
        """Step timeout from input dict is preserved."""
        with patch.object(manager, '_run', new_callable=AsyncMock):
            with patch('src.pipeline.asyncio.create_task', side_effect=lambda coro: coro.close()):
                pl = manager.submit("sequence", [
                    {"agent": "kiro", "prompt": "x", "timeout": 120},
                ])
        assert pl.steps[0].timeout == 120

    def test_submit_respects_output_as(self, manager):
        """output_as from input dict is preserved on step."""
        with patch.object(manager, '_run', new_callable=AsyncMock):
            with patch('src.pipeline.asyncio.create_task', side_effect=lambda coro: coro.close()):
                pl = manager.submit("sequence", [
                    {"agent": "kiro", "prompt": "x", "output_as": "step1_result"},
                ])
        assert pl.steps[0].output_as == "step1_result"


# ============================================================================
# Test Class: Sequence Execution
# ============================================================================

class TestSequenceExecution:
    """Tests for _run_sequence() — steps run in order, context chaining."""

    @pytest.mark.asyncio
    async def test_sequence_runs_steps_in_order(self, manager, mock_pool):
        """Steps execute sequentially; all complete → pipeline completed."""
        conn = _make_mock_conn()
        mock_pool.get_or_create = AsyncMock(return_value=conn)

        pl = Pipeline(pipeline_id="seq-1", mode="sequence", steps=[
            PipelineStep(agent="kiro", prompt_template="step1"),
            PipelineStep(agent="claude", prompt_template="step2"),
        ], context={})

        with patch.object(manager, '_make_shared_cwd', return_value="/tmp/ws"):
            with patch.object(manager, '_webhook_start', new_callable=AsyncMock):
                with patch.object(manager, '_webhook', new_callable=AsyncMock):
                    with patch.object(manager, '_webhook_step', new_callable=AsyncMock):
                        with patch('src.pipeline.get_prompt_suffix', return_value=""):
                            with patch('src.pipeline.transform_notification', side_effect=lambda n: (
                                {"type": "message.part", "content": n["params"]["content"]}
                                if "params" in n and "type" in n.get("params", {}) else None
                            )):
                                await manager._run(pl)

        assert pl.status == "completed"
        assert pl.steps[0].status == "completed"
        assert pl.steps[1].status == "completed"

    @pytest.mark.asyncio
    async def test_sequence_stops_on_step_failure(self, manager, mock_pool):
        """If a step fails, sequence aborts and pipeline status is failed."""
        mock_pool.get_or_create = AsyncMock(side_effect=AcpError("agent down"))

        pl = Pipeline(pipeline_id="seq-2", mode="sequence", steps=[
            PipelineStep(agent="kiro", prompt_template="step1"),
            PipelineStep(agent="claude", prompt_template="step2"),
        ], context={"step_fallback": False})

        with patch.object(manager, '_make_shared_cwd', return_value="/tmp/ws"):
            with patch.object(manager, '_webhook_start', new_callable=AsyncMock):
                with patch.object(manager, '_webhook', new_callable=AsyncMock):
                    with patch.object(manager, '_webhook_step', new_callable=AsyncMock):
                        with patch('src.pipeline.get_prompt_suffix', return_value=""):
                            await manager._run(pl)

        assert pl.status == "failed"
        assert pl.steps[0].status == "failed"
        assert pl.steps[1].status == "pending"  # never reached

    @pytest.mark.asyncio
    async def test_sequence_chains_output_as_context(self, manager, mock_pool):
        """output_as injects step result into context for next step."""
        call_count = 0

        async def fake_prompt(prompt, idle_timeout=300):
            nonlocal call_count
            call_count += 1
            yield {"method": "x", "params": {"type": "message.part", "content": f"result{call_count}"}}
            yield {"_prompt_result": {"result": {"stopReason": "end"}}}

        conn = AsyncMock(spec=AcpConnection)
        conn.session_prompt = fake_prompt
        mock_pool.get_or_create = AsyncMock(return_value=conn)

        pl = Pipeline(pipeline_id="seq-3", mode="sequence", steps=[
            PipelineStep(agent="kiro", prompt_template="analyze", output_as="analysis"),
            PipelineStep(agent="claude", prompt_template="review {{analysis}}"),
        ], context={})

        with patch.object(manager, '_make_shared_cwd', return_value="/tmp/ws"):
            with patch.object(manager, '_webhook_start', new_callable=AsyncMock):
                with patch.object(manager, '_webhook', new_callable=AsyncMock):
                    with patch.object(manager, '_webhook_step', new_callable=AsyncMock):
                        with patch('src.pipeline.get_prompt_suffix', return_value=""):
                            with patch('src.pipeline.transform_notification', side_effect=lambda n: (
                                {"type": "message.part", "content": n["params"]["content"]}
                                if "params" in n and "type" in n.get("params", {}) else None
                            )):
                                await manager._run(pl)

        assert pl.context["analysis"] == "result1"
        assert pl.status == "completed"


# ============================================================================
# Test Class: Parallel Execution
# ============================================================================

class TestParallelExecution:
    """Tests for _run_parallel() — all steps run concurrently."""

    @pytest.mark.asyncio
    async def test_parallel_all_succeed(self, manager, mock_pool):
        """All parallel steps complete → pipeline completed."""
        conn = _make_mock_conn()
        mock_pool.get_or_create = AsyncMock(return_value=conn)

        pl = Pipeline(pipeline_id="par-1", mode="parallel", steps=[
            PipelineStep(agent="kiro", prompt_template="task1"),
            PipelineStep(agent="claude", prompt_template="task2"),
        ], context={})

        with patch.object(manager, '_make_shared_cwd', return_value="/tmp/ws"):
            with patch.object(manager, '_webhook_start', new_callable=AsyncMock):
                with patch.object(manager, '_webhook', new_callable=AsyncMock):
                    with patch.object(manager, '_webhook_step', new_callable=AsyncMock):
                        with patch('src.pipeline.get_prompt_suffix', return_value=""):
                            with patch('src.pipeline.transform_notification', side_effect=lambda n: (
                                {"type": "message.part", "content": n["params"]["content"]}
                                if "params" in n and "type" in n.get("params", {}) else None
                            )):
                                await manager._run(pl)

        assert pl.status == "completed"
        assert all(s.status == "completed" for s in pl.steps)

    @pytest.mark.asyncio
    async def test_parallel_partial_failure(self, manager, mock_pool):
        """One step fails in parallel → pipeline failed, other steps still complete."""
        call_idx = 0

        async def selective_get_or_create(agent, session_id, cwd=""):
            nonlocal call_idx
            call_idx += 1
            if agent == "kiro":
                raise AcpError("kiro down")
            return _make_mock_conn()

        mock_pool.get_or_create = AsyncMock(side_effect=selective_get_or_create)

        pl = Pipeline(pipeline_id="par-2", mode="parallel", steps=[
            PipelineStep(agent="kiro", prompt_template="task1"),
            PipelineStep(agent="claude", prompt_template="task2"),
        ], context={"step_fallback": False})

        with patch.object(manager, '_make_shared_cwd', return_value="/tmp/ws"):
            with patch.object(manager, '_webhook_start', new_callable=AsyncMock):
                with patch.object(manager, '_webhook', new_callable=AsyncMock):
                    with patch.object(manager, '_webhook_step', new_callable=AsyncMock):
                        with patch('src.pipeline.get_prompt_suffix', return_value=""):
                            with patch('src.pipeline.transform_notification', side_effect=lambda n: (
                                {"type": "message.part", "content": n["params"]["content"]}
                                if "params" in n and "type" in n.get("params", {}) else None
                            )):
                                await manager._run(pl)

        assert pl.status == "failed"
        assert pl.steps[0].status == "failed"
        assert pl.steps[1].status == "completed"


# ============================================================================
# Test Class: Race Execution
# ============================================================================

class TestRaceExecution:
    """Tests for _run_race() — first successful step wins."""

    @pytest.mark.asyncio
    async def test_race_first_wins(self, manager, mock_pool):
        """First completed step wins; others are cancelled."""
        conn = _make_mock_conn()
        mock_pool.get_or_create = AsyncMock(return_value=conn)

        pl = Pipeline(pipeline_id="race-1", mode="race", steps=[
            PipelineStep(agent="kiro", prompt_template="task"),
            PipelineStep(agent="claude", prompt_template="task"),
        ], context={})

        with patch.object(manager, '_make_shared_cwd', return_value="/tmp/ws"):
            with patch.object(manager, '_webhook_start', new_callable=AsyncMock):
                with patch.object(manager, '_webhook', new_callable=AsyncMock):
                    with patch.object(manager, '_webhook_step', new_callable=AsyncMock):
                        with patch('src.pipeline.get_prompt_suffix', return_value=""):
                            with patch('src.pipeline.transform_notification', side_effect=lambda n: (
                                {"type": "message.part", "content": n["params"]["content"]}
                                if "params" in n and "type" in n.get("params", {}) else None
                            )):
                                await manager._run(pl)

        assert pl.status == "completed"

    @pytest.mark.asyncio
    async def test_race_all_fail(self, manager, mock_pool):
        """All race steps fail → pipeline failed."""
        mock_pool.get_or_create = AsyncMock(side_effect=AcpError("all down"))

        pl = Pipeline(pipeline_id="race-2", mode="race", steps=[
            PipelineStep(agent="kiro", prompt_template="task"),
            PipelineStep(agent="claude", prompt_template="task"),
        ], context={})

        with patch.object(manager, '_make_shared_cwd', return_value="/tmp/ws"):
            with patch.object(manager, '_webhook_start', new_callable=AsyncMock):
                with patch.object(manager, '_webhook', new_callable=AsyncMock):
                    with patch.object(manager, '_webhook_step', new_callable=AsyncMock):
                        with patch('src.pipeline.get_prompt_suffix', return_value=""):
                            await manager._run(pl)

        assert pl.status == "failed"
        assert "all agents failed" in pl.error


# ============================================================================
# Test Class: Conversation Execution
# ============================================================================

class TestConversationExecution:
    """Tests for _run_conversation() — multi-agent turn-based dialogue."""

    @pytest.mark.asyncio
    async def test_conversation_basic_turns(self, manager, mock_pool):
        """Conversation runs round-robin and stops at max_turns."""
        conn = _make_mock_conn([
            {"method": "x", "params": {"type": "message.part", "content": "I think..."}},
            {"_prompt_result": {"result": {"stopReason": "end"}}},
        ])
        mock_pool.get_or_create = AsyncMock(return_value=conn)

        pl = Pipeline(pipeline_id="conv-1", mode="conversation", steps=[], context={
            "participants": ["kiro", "claude"],
            "topic": "test topic",
            "config": {"max_turns": 3, "stop_conditions": []},
        })

        with patch.object(manager, '_make_shared_cwd', return_value="/tmp/ws"):
            with patch.object(manager, '_webhook_start', new_callable=AsyncMock):
                with patch.object(manager, '_webhook', new_callable=AsyncMock):
                    with patch.object(manager, '_webhook_conversation_turn', new_callable=AsyncMock):
                        with patch('src.pipeline.get_prompt_suffix', return_value=""):
                            with patch('src.pipeline.transform_notification', side_effect=lambda n: (
                                {"type": "message.part", "content": n["params"]["content"]}
                                if "params" in n and "type" in n.get("params", {}) else None
                            )):
                                with patch('src.pipeline._load_prompt', return_value="Topic: {topic}\nAgent: {agent}\nParticipants:\n{participants}\nCWD: {shared_cwd}"):
                                    await manager._run(pl)

        assert pl.status == "completed"
        assert pl.context["turns"] == 3
        assert pl.context["stop_reason"] == "MAX_TURNS"

    @pytest.mark.asyncio
    async def test_conversation_stops_on_done(self, manager, mock_pool):
        """Conversation stops when agent outputs STATUS: DONE."""
        conn = _make_mock_conn([
            {"method": "x", "params": {"type": "message.part", "content": "All good. STATUS: DONE"}},
            {"_prompt_result": {"result": {"stopReason": "end"}}},
        ])
        mock_pool.get_or_create = AsyncMock(return_value=conn)

        pl = Pipeline(pipeline_id="conv-2", mode="conversation", steps=[], context={
            "participants": ["kiro", "claude"],
            "topic": "test",
            "config": {"max_turns": 10, "stop_conditions": ["DONE"]},
        })

        with patch.object(manager, '_make_shared_cwd', return_value="/tmp/ws"):
            with patch.object(manager, '_webhook_start', new_callable=AsyncMock):
                with patch.object(manager, '_webhook', new_callable=AsyncMock):
                    with patch.object(manager, '_webhook_conversation_turn', new_callable=AsyncMock):
                        with patch('src.pipeline.get_prompt_suffix', return_value=""):
                            with patch('src.pipeline.transform_notification', side_effect=lambda n: (
                                {"type": "message.part", "content": n["params"]["content"]}
                                if "params" in n and "type" in n.get("params", {}) else None
                            )):
                                with patch('src.pipeline._load_prompt', return_value="Topic: {topic}\nAgent: {agent}\nParticipants:\n{participants}\nCWD: {shared_cwd}"):
                                    await manager._run(pl)

        assert pl.context["stop_reason"] == "DONE"
        assert pl.context["turns"] == 1

    @pytest.mark.asyncio
    async def test_conversation_mention_routing(self, manager, mock_pool):
        """@mention in output routes next turn to mentioned agent."""
        turn_agents = []

        async def tracking_get_or_create(agent, session_id, cwd=""):
            turn_agents.append(agent)
            return _make_mock_conn([
                {"method": "x", "params": {"type": "message.part",
                    "content": "Let me ask @qwen about this" if agent == "kiro" else "Sure, STATUS: DONE"}},
                {"_prompt_result": {"result": {"stopReason": "end"}}},
            ])

        mock_pool.get_or_create = AsyncMock(side_effect=tracking_get_or_create)

        pl = Pipeline(pipeline_id="conv-3", mode="conversation", steps=[], context={
            "participants": ["kiro", "claude", "qwen"],
            "topic": "test",
            "config": {"max_turns": 5, "stop_conditions": ["DONE"]},
        })

        with patch.object(manager, '_make_shared_cwd', return_value="/tmp/ws"):
            with patch.object(manager, '_webhook_start', new_callable=AsyncMock):
                with patch.object(manager, '_webhook', new_callable=AsyncMock):
                    with patch.object(manager, '_webhook_conversation_turn', new_callable=AsyncMock):
                        with patch('src.pipeline.get_prompt_suffix', return_value=""):
                            with patch('src.pipeline.transform_notification', side_effect=lambda n: (
                                {"type": "message.part", "content": n["params"]["content"]}
                                if "params" in n and "type" in n.get("params", {}) else None
                            )):
                                with patch('src.pipeline._load_prompt', return_value="Topic: {topic}\nAgent: {agent}\nParticipants:\n{participants}\nCWD: {shared_cwd}"):
                                    await manager._run(pl)

        # Turn 1: kiro (round-robin start), Turn 2: qwen (via @mention)
        assert turn_agents[0] == "kiro"
        assert turn_agents[1] == "qwen"


# ============================================================================
# Test Class: Error Handling & Edge Cases
# ============================================================================

class TestPipelineErrorHandling:
    """Tests for error paths: unknown mode, pool exhaustion, exceptions."""

    @pytest.mark.asyncio
    async def test_unknown_mode_fails(self, manager):
        """Unknown pipeline mode → failed with error message."""
        pl = Pipeline(pipeline_id="err-1", mode="unknown_mode", steps=[], context={})

        with patch.object(manager, '_make_shared_cwd', return_value="/tmp/ws"):
            with patch.object(manager, '_webhook_start', new_callable=AsyncMock):
                with patch.object(manager, '_webhook', new_callable=AsyncMock):
                    await manager._run(pl)

        assert pl.status == "failed"
        assert "unknown mode" in pl.error

    @pytest.mark.asyncio
    async def test_pool_exhausted_fails_step(self, manager, mock_pool):
        """PoolExhaustedError during step → step failed."""
        mock_pool.get_or_create = AsyncMock(side_effect=PoolExhaustedError("no slots"))

        pl = Pipeline(pipeline_id="err-2", mode="sequence", steps=[
            PipelineStep(agent="kiro", prompt_template="x"),
        ], context={})

        with patch.object(manager, '_make_shared_cwd', return_value="/tmp/ws"):
            with patch.object(manager, '_webhook_start', new_callable=AsyncMock):
                with patch.object(manager, '_webhook', new_callable=AsyncMock):
                    with patch.object(manager, '_webhook_step', new_callable=AsyncMock):
                        with patch('src.pipeline.get_prompt_suffix', return_value=""):
                            await manager._run(pl)

        assert pl.status == "failed"
        assert pl.steps[0].status == "failed"
        assert "no slots" in pl.steps[0].error

    @pytest.mark.asyncio
    async def test_run_sets_completed_at(self, manager, mock_pool):
        """_run always sets completed_at regardless of success/failure."""
        mock_pool.get_or_create = AsyncMock(side_effect=AcpError("boom"))

        pl = Pipeline(pipeline_id="err-3", mode="sequence", steps=[
            PipelineStep(agent="kiro", prompt_template="x"),
        ], context={})

        with patch.object(manager, '_make_shared_cwd', return_value="/tmp/ws"):
            with patch.object(manager, '_webhook_start', new_callable=AsyncMock):
                with patch.object(manager, '_webhook', new_callable=AsyncMock):
                    with patch.object(manager, '_webhook_step', new_callable=AsyncMock):
                        with patch('src.pipeline.get_prompt_suffix', return_value=""):
                            await manager._run(pl)

        assert pl.completed_at > 0


# ============================================================================
# Test Class: Webhook Integration
# ============================================================================

class TestWebhookIntegration:
    """Tests for webhook notifications at pipeline lifecycle events."""

    @pytest.mark.asyncio
    async def test_no_webhook_when_url_empty(self, manager, mock_pool):
        """No webhook calls when webhook_url is empty."""
        conn = _make_mock_conn()
        mock_pool.get_or_create = AsyncMock(return_value=conn)

        pl = Pipeline(pipeline_id="wh-1", mode="sequence", steps=[
            PipelineStep(agent="kiro", prompt_template="x"),
        ], context={}, webhook_meta={"target": "someone"})

        with patch.object(manager._sender, 'send', new_callable=AsyncMock) as mock_send:
            with patch.object(manager, '_make_shared_cwd', return_value="/tmp/ws"):
                with patch('src.pipeline.get_prompt_suffix', return_value=""):
                    with patch('src.pipeline.transform_notification', side_effect=lambda n: (
                        {"type": "message.part", "content": n["params"]["content"]}
                        if "params" in n and "type" in n.get("params", {}) else None
                    )):
                        await manager._run(pl)

        mock_send.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_webhook_when_no_target(self, manager):
        """No webhook calls when webhook_meta has no target."""
        pl = Pipeline(pipeline_id="wh-2", mode="sequence", steps=[], webhook_meta={})
        await manager._webhook_start(pl)


# ============================================================================
# Test Class: Stats Aggregation
# ============================================================================

class TestPipelineStats:
    """Tests for stats() — aggregate pipeline metrics."""

    def test_stats_empty(self, manager):
        """stats() returns empty modes when no pipelines exist."""
        result = manager.stats(hours=24)
        assert result["modes"] == {}

    def test_stats_counts_by_mode(self, manager, mock_pool):
        """stats() correctly groups by mode."""
        # Directly save pipelines to store
        for i, mode in enumerate(["sequence", "sequence", "parallel"]):
            pl = Pipeline(pipeline_id=f"stat-{i}", mode=mode, steps=[],
                          status="completed", completed_at=time.time())
            manager._store.save(pl)

        result = manager.stats(hours=1)
        assert result["modes"]["sequence"]["total"] == 2
        assert result["modes"]["parallel"]["total"] == 1


# ============================================================================
# P0: PipelineManager — sequence, parallel, race, exec_step, error handling
# (merged from test_pipeline_p0.py)
# ============================================================================

def _make_manager(tmp_path, agents_cfg=None):
    pool = Mock()
    pool._connections = {}
    cfg = agents_cfg or {
        "kiro": {"command": "echo", "working_dir": "/tmp", "description": "kiro"},
        "claude": {"command": "echo", "working_dir": "/tmp", "description": "claude"},
        "qwen": {"command": "echo", "working_dir": "/tmp", "description": "qwen"},
    }
    return PipelineManager(pool=pool, agents_cfg=cfg, db_path=str(tmp_path / "test.db")), pool


def _mock_conn(text="ok"):
    conn = AsyncMock(spec=AcpConnection)
    async def fake_prompt(prompt, idle_timeout=300):
        yield {"method": "x", "params": {"type": "message.part", "content": text}}
        yield {"_prompt_result": {"result": {"stopReason": "end"}}}
    conn.session_prompt = fake_prompt
    return conn


_PATCHES = [
    patch("src.pipeline.get_prompt_suffix", return_value=""),
    patch("src.pipeline.transform_notification", side_effect=lambda n: (
        {"type": "message.part", "content": n["params"]["content"]}
        if "params" in n and "type" in n.get("params", {}) else None
    )),
]


@pytest.mark.asyncio
async def test_submit_sequence_success(tmp_path):
    mgr, pool = _make_manager(tmp_path)
    call_order = []
    async def tracking_get_or_create(agent, sid, cwd=""):
        call_order.append(agent)
        return _mock_conn(f"result_from_{agent}")
    pool.get_or_create = AsyncMock(side_effect=tracking_get_or_create)
    pl = Pipeline(pipeline_id="p1", mode="sequence", steps=[
        PipelineStep(agent="kiro", prompt_template="step1", output_as="step1_out"),
        PipelineStep(agent="claude", prompt_template="review {{step1_out}}"),
    ], context={})
    for p in _PATCHES: p.start()
    try:
        with patch.object(mgr, "_make_shared_cwd", return_value="/tmp/ws"):
            with patch.object(mgr, "_webhook_start", new_callable=AsyncMock):
                with patch.object(mgr, "_webhook", new_callable=AsyncMock):
                    with patch.object(mgr, "_webhook_step", new_callable=AsyncMock):
                        await mgr._run(pl)
    finally:
        for p in _PATCHES: p.stop()
    assert call_order == ["kiro", "claude"]
    assert pl.context["step1_out"] == "result_from_kiro"
    assert pl.status == "completed"


@pytest.mark.asyncio
async def test_submit_parallel_success(tmp_path):
    mgr, pool = _make_manager(tmp_path)
    pool.get_or_create = AsyncMock(side_effect=lambda a, s, cwd="": _mock_conn(f"out_{a}"))
    pl = Pipeline(pipeline_id="p2", mode="parallel", steps=[
        PipelineStep(agent="kiro", prompt_template="t1"),
        PipelineStep(agent="claude", prompt_template="t2"),
        PipelineStep(agent="qwen", prompt_template="t3"),
    ], context={})
    for p in _PATCHES: p.start()
    try:
        with patch.object(mgr, "_make_shared_cwd", return_value="/tmp/ws"):
            with patch.object(mgr, "_webhook_start", new_callable=AsyncMock):
                with patch.object(mgr, "_webhook", new_callable=AsyncMock):
                    with patch.object(mgr, "_webhook_step", new_callable=AsyncMock):
                        await mgr._run(pl)
    finally:
        for p in _PATCHES: p.stop()
    assert pl.status == "completed"
    assert all(s.status == "completed" for s in pl.steps)


@pytest.mark.asyncio
async def test_submit_race_first_wins(tmp_path):
    mgr, pool = _make_manager(tmp_path)
    async def speed_get_or_create(agent, sid, cwd=""):
        if agent == "qwen": await asyncio.sleep(0.05)
        return _mock_conn(f"win_{agent}")
    pool.get_or_create = AsyncMock(side_effect=speed_get_or_create)
    pl = Pipeline(pipeline_id="p3", mode="race", steps=[
        PipelineStep(agent="kiro", prompt_template="t"),
        PipelineStep(agent="qwen", prompt_template="t"),
    ], context={})
    for p in _PATCHES: p.start()
    try:
        with patch.object(mgr, "_make_shared_cwd", return_value="/tmp/ws"):
            with patch.object(mgr, "_webhook_start", new_callable=AsyncMock):
                with patch.object(mgr, "_webhook", new_callable=AsyncMock):
                    with patch.object(mgr, "_webhook_step", new_callable=AsyncMock):
                        await mgr._run(pl)
    finally:
        for p in _PATCHES: p.stop()
    assert pl.status == "completed"
    assert any(s.status == "completed" for s in pl.steps)


@pytest.mark.asyncio
async def test_exec_step_agent_success(tmp_path):
    mgr, pool = _make_manager(tmp_path)
    pool.get_or_create = AsyncMock(return_value=_mock_conn("agent_response"))
    pl = Pipeline(pipeline_id="p4", mode="sequence", steps=[], context={"shared_cwd": "/tmp/ws"})
    step = PipelineStep(agent="claude", prompt_template="test prompt")
    pl.steps.append(step)
    for p in _PATCHES: p.start()
    try:
        await mgr._exec_step(pl, step, "test prompt")
    finally:
        for p in _PATCHES: p.stop()
    assert step.status == "completed"
    assert step.result == "agent_response"


@pytest.mark.asyncio
async def test_exec_step_no_builtin_retry(tmp_path):
    mgr, pool = _make_manager(tmp_path)
    pool.get_or_create = AsyncMock(side_effect=AcpError("agent down"))
    pl = Pipeline(pipeline_id="p5", mode="sequence", steps=[],
                  context={"shared_cwd": "/tmp/ws", "step_fallback": False})
    step = PipelineStep(agent="claude", prompt_template="x")
    pl.steps.append(step)
    for p in _PATCHES: p.start()
    try:
        await mgr._exec_step(pl, step, "x")
    finally:
        for p in _PATCHES: p.stop()
    assert step.status == "failed"
    assert "agent down" in step.error
    assert pool.get_or_create.await_count == 1


@pytest.mark.asyncio
async def test_sequence_stops_on_step_failure(tmp_path):
    mgr, pool = _make_manager(tmp_path)
    async def fail_on_claude(agent, sid, cwd=""):
        if agent == "claude": raise AcpError("claude down")
        return _mock_conn("ok")
    pool.get_or_create = AsyncMock(side_effect=fail_on_claude)
    pl = Pipeline(pipeline_id="p6", mode="sequence", steps=[
        PipelineStep(agent="kiro", prompt_template="s1"),
        PipelineStep(agent="claude", prompt_template="s2"),
        PipelineStep(agent="qwen", prompt_template="s3"),
    ], context={"step_fallback": False})
    for p in _PATCHES: p.start()
    try:
        with patch.object(mgr, "_make_shared_cwd", return_value="/tmp/ws"):
            with patch.object(mgr, "_webhook_start", new_callable=AsyncMock):
                with patch.object(mgr, "_webhook", new_callable=AsyncMock):
                    with patch.object(mgr, "_webhook_step", new_callable=AsyncMock):
                        await mgr._run(pl)
    finally:
        for p in _PATCHES: p.stop()
    assert pl.status == "failed"
    assert pl.steps[0].status == "completed"
    assert pl.steps[1].status == "failed"
    assert pl.steps[2].status == "pending"


@pytest.mark.asyncio
async def test_parallel_partial_failure(tmp_path):
    mgr, pool = _make_manager(tmp_path)
    async def selective(agent, sid, cwd=""):
        if agent == "claude": raise AcpError("claude error")
        return _mock_conn(f"ok_{agent}")
    pool.get_or_create = AsyncMock(side_effect=selective)
    pl = Pipeline(pipeline_id="p7", mode="parallel", steps=[
        PipelineStep(agent="kiro", prompt_template="t1"),
        PipelineStep(agent="claude", prompt_template="t2"),
        PipelineStep(agent="qwen", prompt_template="t3"),
    ], context={"step_fallback": False})
    for p in _PATCHES: p.start()
    try:
        with patch.object(mgr, "_make_shared_cwd", return_value="/tmp/ws"):
            with patch.object(mgr, "_webhook_start", new_callable=AsyncMock):
                with patch.object(mgr, "_webhook", new_callable=AsyncMock):
                    with patch.object(mgr, "_webhook_step", new_callable=AsyncMock):
                        await mgr._run(pl)
    finally:
        for p in _PATCHES: p.stop()
    assert pl.status == "failed"
    assert pl.steps[0].status == "completed"
    assert pl.steps[1].status == "failed"
    assert pl.steps[2].status == "completed"


@pytest.mark.asyncio
async def test_step_fallback_succeeds_and_persists_history(tmp_path):
    mgr, pool = _make_manager(tmp_path)

    async def fail_then_succeed(agent, sid, cwd=""):
        if agent == "claude":
            raise AcpError("claude down")
        return _mock_conn("fallback result")

    pool.get_or_create = AsyncMock(side_effect=fail_then_succeed)
    pl = Pipeline(pipeline_id="fallback-1", mode="sequence", steps=[
        PipelineStep(agent="claude", prompt_template="task"),
    ], context={})

    for p in _PATCHES: p.start()
    try:
        with patch("src.pipeline.get_best_fallback", return_value="kiro"):
            with patch.object(mgr, "_make_shared_cwd", return_value="/tmp/ws"):
                with patch.object(mgr, "_webhook_start", new_callable=AsyncMock):
                    with patch.object(mgr, "_webhook", new_callable=AsyncMock):
                        with patch.object(mgr, "_webhook_step", new_callable=AsyncMock):
                            await mgr._run(pl)
    finally:
        for p in _PATCHES: p.stop()

    step = pl.steps[0]
    assert pl.status == "completed"
    assert step.agent == "kiro"
    assert step.original_agent == "claude"
    assert step.fallback_history == ["claude"]
    assert step.result == "fallback result"
    assert "step_fallback" in [e["event"] for e in pl._event_history]

    reloaded, _ = _make_manager(tmp_path)
    restored = reloaded.get(pl.pipeline_id)
    assert restored.steps[0].original_agent == "claude"
    assert restored.steps[0].fallback_history == ["claude"]


@pytest.mark.asyncio
async def test_step_fallback_stops_after_two_attempts(tmp_path):
    mgr, pool = _make_manager(tmp_path)
    pool.get_or_create = AsyncMock(side_effect=AcpError("agent down"))
    pl = Pipeline(pipeline_id="fallback-2", mode="sequence", steps=[
        PipelineStep(agent="claude", prompt_template="task"),
    ], context={})

    for p in _PATCHES: p.start()
    try:
        with patch("src.pipeline.get_best_fallback", side_effect=["kiro", "qwen"]):
            await mgr._exec_step(pl, pl.steps[0], "task")
    finally:
        for p in _PATCHES: p.stop()

    step = pl.steps[0]
    assert step.status == "failed"
    assert step.original_agent == "claude"
    assert step.agent == "qwen"
    assert step.fallback_history == ["claude", "kiro"]
    assert pool.get_or_create.await_count == 3


def _close_scheduled(coros):
    def close(coro):
        coros.append(coro)
        coro.close()
        return Mock()
    return close


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["sequence", "parallel"])
async def test_recovery_resumes_only_unfinished_steps(tmp_path, mode):
    db_path = str(tmp_path / f"recovery-{mode}.db")
    original, _ = _make_manager(tmp_path)
    original._store = original._store.__class__(db_path)
    pl = Pipeline(
        pipeline_id=f"recovery-{mode}", mode=mode, status="running",
        context={"shared_cwd": str(tmp_path)},
        steps=[
            PipelineStep(agent="kiro", prompt_template="done", output_as="first",
                         timeout=77, status="completed", result="kept"),
            PipelineStep(agent="claude", prompt_template="todo", timeout=88,
                         status="running", result="partial", error="interrupted"),
        ],
    )
    original._store.save(pl)

    recovered, _ = _make_manager(tmp_path)
    recovered._store = recovered._store.__class__(db_path)
    scheduled = []
    with patch("src.pipeline.asyncio.create_task", side_effect=_close_scheduled(scheduled)):
        await recovered.run_recovery()

    restored = recovered._pipelines[pl.pipeline_id]
    assert restored.retries == 1
    assert restored.status == "pending"
    assert restored.steps[0].status == "completed"
    assert restored.steps[0].result == "kept"
    assert restored.steps[0].timeout == 77
    assert restored.steps[1].status == "pending"
    assert restored.steps[1].error == ""
    assert restored.steps[1].timeout == 88
    assert restored.context["first"] == "kept"
    assert len(scheduled) == 1


@pytest.mark.asyncio
async def test_recovery_resets_all_race_steps(tmp_path):
    db_path = str(tmp_path / "recovery-race.db")
    original, _ = _make_manager(tmp_path)
    original._store = original._store.__class__(db_path)
    pl = Pipeline(pipeline_id="recovery-race", mode="race", status="running", steps=[
        PipelineStep(agent="kiro", prompt_template="a", status="completed", result="old winner"),
        PipelineStep(agent="claude", prompt_template="b", status="running", result="partial"),
    ])
    original._store.save(pl)

    recovered, _ = _make_manager(tmp_path)
    recovered._store = recovered._store.__class__(db_path)
    scheduled = []
    with patch("src.pipeline.asyncio.create_task", side_effect=_close_scheduled(scheduled)):
        await recovered.run_recovery()

    restored = recovered._pipelines[pl.pipeline_id]
    assert [s.status for s in restored.steps] == ["pending", "pending"]
    assert [s.result for s in restored.steps] == ["", ""]
    assert len(scheduled) == 1


@pytest.mark.asyncio
async def test_recovery_fails_conversation_with_terminal_event(tmp_path):
    db_path = str(tmp_path / "recovery-conversation.db")
    original, _ = _make_manager(tmp_path)
    original._store = original._store.__class__(db_path)
    pl = Pipeline(pipeline_id="recovery-conversation", mode="conversation",
                  status="running", steps=[])
    original._store.save(pl)

    recovered, _ = _make_manager(tmp_path)
    recovered._store = recovered._store.__class__(db_path)
    with patch.object(recovered, "_webhook", new_callable=AsyncMock):
        await recovered.run_recovery()

    restored = recovered.get(pl.pipeline_id)
    assert restored.status == "failed"
    assert "not resumable" in restored.error
    assert restored._event_history[-1]["event"] == "pipeline_done"


@pytest.mark.asyncio
async def test_recovery_stops_after_retry_limit(tmp_path):
    db_path = str(tmp_path / "recovery-limit.db")
    original, _ = _make_manager(tmp_path)
    original._store = original._store.__class__(db_path)
    pl = Pipeline(pipeline_id="recovery-limit", mode="sequence", status="running",
                  retries=2, steps=[PipelineStep(agent="kiro", prompt_template="task")])
    original._store.save(pl)

    recovered, _ = _make_manager(tmp_path)
    recovered._store = recovered._store.__class__(db_path)
    with patch.object(recovered, "_webhook", new_callable=AsyncMock):
        await recovered.run_recovery()

    restored = recovered.get(pl.pipeline_id)
    assert restored.status == "failed"
    assert restored.retries == 3
    assert "2 recovery retries" in restored.error
    assert restored._event_history[-1]["event"] == "pipeline_done"


def test_lifecycle_events_persist_but_progress_does_not(tmp_path):
    mgr, _ = _make_manager(tmp_path)
    pl = Pipeline(pipeline_id="events-1", mode="sequence", steps=[])
    mgr._store.save(pl)
    mgr._emit_event(pl, "step_started", {"index": 0, "agent": "kiro"})
    mgr._emit_event(pl, "step_progress", {"index": 0, "agent": "kiro", "kind": "status"})

    reloaded, _ = _make_manager(tmp_path)
    restored = reloaded.get(pl.pipeline_id)
    assert [e["event"] for e in restored._event_history] == ["step_started"]


@pytest.mark.asyncio
async def test_invalid_mode_raises_error(tmp_path):
    mgr, pool = _make_manager(tmp_path)
    pl = Pipeline(pipeline_id="p8", mode="invalid_mode", steps=[
        PipelineStep(agent="kiro", prompt_template="x"),
    ], context={})
    with patch.object(mgr, "_make_shared_cwd", return_value="/tmp/ws"):
        with patch.object(mgr, "_webhook_start", new_callable=AsyncMock):
            with patch.object(mgr, "_webhook", new_callable=AsyncMock):
                await mgr._run(pl)
    assert pl.status == "failed"
    assert "unknown mode" in pl.error


# ============================================================================
# P1: PipelineManager — conversation, webhook, cwd isolation, template, timeout
# (merged from test_pipeline_p1.py)
# ============================================================================

def _mgr(tmp_path, webhook_url="", agents_cfg=None):
    pool = Mock()
    pool._connections = {}
    cfg = agents_cfg or {
        "kiro": {"command": "echo", "working_dir": "/tmp", "description": "kiro agent"},
        "claude": {"command": "echo", "working_dir": "/tmp", "description": "claude agent"},
        "qwen": {"command": "echo", "working_dir": "/tmp", "description": "qwen agent"},
    }
    return PipelineManager(pool=pool, agents_cfg=cfg, webhook_url=webhook_url,
                           db_path=str(tmp_path / "test.db")), pool


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


@pytest.mark.asyncio
async def test_conversation_mode_multi_turn(tmp_path):
    mgr, pool = _mgr(tmp_path)
    turn_num = 0
    async def get_conn(agent, sid, cwd=""):
        nonlocal turn_num
        turn_num += 1
        return _conn(f"turn{turn_num} by {agent}")
    pool.get_or_create = AsyncMock(side_effect=get_conn)
    pl = Pipeline(pipeline_id="c1", mode="conversation", steps=[], context={
        "participants": ["kiro", "claude"],
        "topic": "design review",
        "config": {"max_turns": 4, "stop_conditions": [], "a2a_rules": False},
    })
    with _TN, _PS, _LP:
        with patch.object(mgr, "_make_shared_cwd", return_value="/tmp/ws"):
            with patch.object(mgr, "_webhook_start", new_callable=AsyncMock):
                with patch.object(mgr, "_webhook", new_callable=AsyncMock):
                    with patch.object(mgr, "_webhook_conversation_turn", new_callable=AsyncMock):
                        await mgr._run(pl)
    assert pl.status == "completed"
    assert pl.context["turns"] == 4
    assert len(pl.context["transcript"]) == 4


@pytest.mark.asyncio
async def test_webhook_callback_on_step_complete(tmp_path):
    mgr, pool = _mgr(tmp_path, webhook_url="https://hook.example.com/cb")
    pool.get_or_create = AsyncMock(return_value=_conn("done"))
    pl = Pipeline(pipeline_id="w1", mode="sequence", steps=[
        PipelineStep(agent="kiro", prompt_template="s1"),
        PipelineStep(agent="claude", prompt_template="s2"),
    ], context={}, webhook_meta={"target": "user1"})
    with _TN, _PS:
        with patch.object(mgr, "_make_shared_cwd", return_value="/tmp/ws"):
            with patch.object(mgr._sender, "send", new_callable=AsyncMock) as mock_send:
                await mgr._run(pl)
    assert pl.status == "completed"
    assert mock_send.await_count == 4  # start + step1 + step2 + final


@pytest.mark.asyncio
async def test_shared_cwd_isolation(tmp_path):
    mgr, pool = _mgr(tmp_path)
    cwds_seen = []
    async def track_cwd(agent, sid, cwd=""):
        cwds_seen.append((agent, cwd))
        return _conn("ok")
    pool.get_or_create = AsyncMock(side_effect=track_cwd)
    import os
    shared = str(tmp_path / "workspace")
    pl = Pipeline(pipeline_id="iso1", mode="parallel", steps=[
        PipelineStep(agent="kiro", prompt_template="t1"),
        PipelineStep(agent="claude", prompt_template="t2"),
    ], context={})
    def fake_make_cwd(p):
        p.context["shared_cwd"] = shared
        return shared
    with _TN, _PS:
        with patch.object(mgr, "_make_shared_cwd", side_effect=fake_make_cwd):
            with patch.object(mgr, "_webhook_start", new_callable=AsyncMock):
                with patch.object(mgr, "_webhook", new_callable=AsyncMock):
                    with patch.object(mgr, "_webhook_step", new_callable=AsyncMock):
                        await mgr._run(pl)
    agents_and_cwds = {a: c for a, c in cwds_seen}
    assert agents_and_cwds["kiro"] == os.path.join(shared, "kiro")
    assert agents_and_cwds["claude"] == os.path.join(shared, "claude")


def test_context_template_render(tmp_path):
    mgr, _ = _mgr(tmp_path)
    assert mgr._render("Hello {{name}}", {"name": "world"}) == "Hello world"
    assert mgr._render("{{a}}+{{b}}", {"a": "1", "b": "2"}) == "1+2"
    assert mgr._render("{{missing}}", {}) == "{{missing}}"


@pytest.mark.asyncio
async def test_timeout_per_step_enforcement(tmp_path):
    mgr, pool = _mgr(tmp_path)
    async def hanging_conn(agent, sid, cwd=""):
        conn = AsyncMock(spec=AcpConnection)
        async def hang_forever(p, idle_timeout=300):
            await asyncio.sleep(999)
            yield {"_prompt_result": {"result": {"stopReason": "end"}}}
        conn.session_prompt = hang_forever
        return conn
    pool.get_or_create = AsyncMock(side_effect=hanging_conn)
    pl = Pipeline(pipeline_id="t1", mode="sequence", steps=[
        PipelineStep(agent="kiro", prompt_template="x", timeout=0.1),
    ], context={})
    with _TN, _PS:
        with patch.object(mgr, "_make_shared_cwd", return_value="/tmp/ws"):
            with patch.object(mgr, "_webhook_start", new_callable=AsyncMock):
                with patch.object(mgr, "_webhook", new_callable=AsyncMock):
                    with patch.object(mgr, "_webhook_step", new_callable=AsyncMock):
                        await mgr._run(pl)
    assert pl.steps[0].status == "failed"
    assert "timeout" in pl.steps[0].error


@pytest.mark.asyncio
async def test_empty_steps_handling(tmp_path):
    for mode in ("sequence", "parallel", "race"):
        mgr, pool = _mgr(tmp_path)
        pl = Pipeline(pipeline_id=f"empty-{mode}", mode=mode, steps=[], context={})
        with patch.object(mgr, "_make_shared_cwd", return_value="/tmp/ws"):
            with patch.object(mgr, "_webhook_start", new_callable=AsyncMock):
                with patch.object(mgr, "_webhook", new_callable=AsyncMock):
                    await mgr._run(pl)
        if mode == "race":
            assert pl.status == "failed"
        else:
            assert pl.status == "completed"


@pytest.mark.asyncio
async def test_step_output_size_limit(tmp_path):
    mgr, pool = _mgr(tmp_path)
    big_text = "x" * (2 * 1024 * 1024)
    pool.get_or_create = AsyncMock(return_value=_conn(big_text))
    pl = Pipeline(pipeline_id="big1", mode="sequence", steps=[
        PipelineStep(agent="kiro", prompt_template="t"),
    ], context={})
    with _TN, _PS:
        with patch.object(mgr, "_make_shared_cwd", return_value="/tmp/ws"):
            with patch.object(mgr, "_webhook_start", new_callable=AsyncMock):
                with patch.object(mgr, "_webhook", new_callable=AsyncMock):
                    with patch.object(mgr, "_webhook_step", new_callable=AsyncMock):
                        await mgr._run(pl)
    assert pl.status == "completed"
    from src.pipeline import MAX_OUTPUT_SIZE
    assert len(pl.steps[0].result) < len(big_text)


@pytest.mark.asyncio
async def test_cancel_pipeline_mid_execution(tmp_path):
    mgr, pool = _mgr(tmp_path)
    step_started = asyncio.Event()
    async def slow_get_or_create(agent, sid, cwd=""):
        if agent == "claude":
            step_started.set()
            await asyncio.sleep(10)
        return _conn("ok")
    pool.get_or_create = AsyncMock(side_effect=slow_get_or_create)
    pl = Pipeline(pipeline_id="cancel1", mode="sequence", steps=[
        PipelineStep(agent="kiro", prompt_template="s1"),
        PipelineStep(agent="claude", prompt_template="s2"),
        PipelineStep(agent="qwen", prompt_template="s3"),
    ], context={})
    with _TN, _PS:
        with patch.object(mgr, "_make_shared_cwd", return_value="/tmp/ws"):
            with patch.object(mgr, "_webhook_start", new_callable=AsyncMock):
                with patch.object(mgr, "_webhook", new_callable=AsyncMock):
                    with patch.object(mgr, "_webhook_step", new_callable=AsyncMock):
                        task = asyncio.create_task(mgr._run(pl))
                        await step_started.wait()
                        task.cancel()
                        with pytest.raises(asyncio.CancelledError):
                            await task
    assert pl.steps[0].status == "completed"
    assert pl.steps[2].status == "pending"


# ============================================================================
# Test Class: Step Progress Events (v0.22.0)
# ============================================================================

class TestStepProgressEvents:
    """Tests for step_progress SSE event emission and tools/thinking accumulation
    introduced in v0.22.0. The implementation adds new branches inside the
    notification loop in `_exec_step_acp` without changing existing message.part
    handling.
    """

    @pytest.mark.asyncio
    async def test_step_progress_emits_for_all_kinds(self, manager, mock_pool):
        """All 5 transformed kinds → step_progress event emitted with metadata."""
        # Build raw notifications; we'll have transform_notification return
        # canned events so we can assert the wiring without coupling to sse.py.
        canned_events = [
            {"type": "tool.start", "toolCallId": "tc-1", "title": "read_file", "status": "pending"},
            {"type": "message.thinking", "content": "thinking..."},
            {"type": "tool.done", "toolCallId": "tc-1", "title": "read_file", "status": "completed"},
            {"type": "message.part", "content": "hello"},
            {"type": "status", "text": "step 1 of 2"},
        ]
        notifications = [{"params": {"i": i}} for i in range(len(canned_events))] + [
            {"_prompt_result": {"result": {"stopReason": "end"}}}
        ]

        async def fake_prompt(prompt, idle_timeout=300):
            for n in notifications:
                yield n

        conn = AsyncMock(spec=AcpConnection)
        conn.session_prompt = fake_prompt
        mock_pool.get_or_create = AsyncMock(return_value=conn)

        # transform_notification: index into canned_events by params.i; sentinel returns None
        def fake_transform(n):
            if "_prompt_result" in n:
                return None
            return canned_events[n["params"]["i"]]

        pl = Pipeline(pipeline_id="prog-1", mode="sequence", steps=[
            PipelineStep(agent="kiro", prompt_template="go"),
        ], context={})

        with patch.object(manager, '_make_shared_cwd', return_value="/tmp/ws"):
            with patch.object(manager, '_webhook_start', new_callable=AsyncMock):
                with patch.object(manager, '_webhook', new_callable=AsyncMock):
                    with patch.object(manager, '_webhook_step', new_callable=AsyncMock):
                        with patch('src.pipeline.get_prompt_suffix', return_value=""):
                            with patch('src.pipeline.transform_notification', side_effect=fake_transform):
                                await manager._run(pl)

        # Step completed normally
        assert pl.steps[0].status == "completed"
        # message.part path unchanged: result is concat of message.part contents
        assert pl.steps[0].result == "hello"
        # thinking accumulated
        assert "".join(pl.steps[0]._thinking_parts) == "thinking..."
        # tools tracked, tc-1 transitioned start→done
        assert pl.steps[0].tools == [{"id": "tc-1", "name": "read_file", "status": "completed"}]

        # Verify step_progress events landed on the SSE history (5 emits, one per kind)
        progress_events = [e for e in pl._event_history if e["event"] == "step_progress"]
        assert len(progress_events) == 5
        kinds = [e["data"]["kind"] for e in progress_events]
        assert kinds == ["tool.start", "message.thinking", "tool.done", "message.part", "status"]
        # Each event carries the step index and agent
        for e in progress_events:
            assert e["data"]["index"] == 0
            assert e["data"]["agent"] == "kiro"
            assert "_emitted_at" in e["data"]

    @pytest.mark.asyncio
    async def test_step_progress_orphan_tool_done(self, manager, mock_pool):
        """A tool.done with no prior tool.start is appended to tools (resilience)."""
        canned_events = [
            {"type": "tool.done", "toolCallId": "tc-orphan", "title": "ghost", "status": "failed"},
        ]
        notifications = [{"params": {"i": 0}},
                         {"_prompt_result": {"result": {"stopReason": "end"}}}]

        async def fake_prompt(prompt, idle_timeout=300):
            for n in notifications:
                yield n

        conn = AsyncMock(spec=AcpConnection)
        conn.session_prompt = fake_prompt
        mock_pool.get_or_create = AsyncMock(return_value=conn)

        def fake_transform(n):
            if "_prompt_result" in n:
                return None
            return canned_events[n["params"]["i"]]

        pl = Pipeline(pipeline_id="prog-orphan", mode="sequence", steps=[
            PipelineStep(agent="kiro", prompt_template="go"),
        ], context={})

        with patch.object(manager, '_make_shared_cwd', return_value="/tmp/ws"):
            with patch.object(manager, '_webhook_start', new_callable=AsyncMock):
                with patch.object(manager, '_webhook', new_callable=AsyncMock):
                    with patch.object(manager, '_webhook_step', new_callable=AsyncMock):
                        with patch('src.pipeline.get_prompt_suffix', return_value=""):
                            with patch('src.pipeline.transform_notification', side_effect=fake_transform):
                                await manager._run(pl)

        assert pl.steps[0].status == "completed"
        assert pl.steps[0].tools == [{"id": "tc-orphan", "name": "ghost", "status": "failed"}]

    def test_step_to_dict_includes_tools(self):
        """to_dict() exposes tools when non-empty, omits when empty."""
        s1 = PipelineStep(agent="kiro", prompt_template="x")
        # Empty tools → not in dict
        assert "tools" not in s1.to_dict()

        s2 = PipelineStep(agent="kiro", prompt_template="x")
        s2.tools = [{"id": "t1", "name": "read", "status": "completed"}]
        d = s2.to_dict()
        assert d["tools"] == [{"id": "t1", "name": "read", "status": "completed"}]

    @pytest.mark.asyncio
    async def test_message_part_path_unchanged(self, manager, mock_pool):
        """Regression: the existing message.part → step.result path is preserved."""
        # Only message.part events; result must equal their concatenation
        canned_events = [
            {"type": "message.part", "content": "Hello "},
            {"type": "message.part", "content": "world"},
        ]
        notifications = [{"params": {"i": 0}}, {"params": {"i": 1}},
                         {"_prompt_result": {"result": {"stopReason": "end"}}}]

        async def fake_prompt(prompt, idle_timeout=300):
            for n in notifications:
                yield n

        conn = AsyncMock(spec=AcpConnection)
        conn.session_prompt = fake_prompt
        mock_pool.get_or_create = AsyncMock(return_value=conn)

        def fake_transform(n):
            if "_prompt_result" in n:
                return None
            return canned_events[n["params"]["i"]]

        pl = Pipeline(pipeline_id="regress-1", mode="sequence", steps=[
            PipelineStep(agent="kiro", prompt_template="go"),
        ], context={})

        with patch.object(manager, '_make_shared_cwd', return_value="/tmp/ws"):
            with patch.object(manager, '_webhook_start', new_callable=AsyncMock):
                with patch.object(manager, '_webhook', new_callable=AsyncMock):
                    with patch.object(manager, '_webhook_step', new_callable=AsyncMock):
                        with patch('src.pipeline.get_prompt_suffix', return_value=""):
                            with patch('src.pipeline.transform_notification', side_effect=fake_transform):
                                await manager._run(pl)

        assert pl.steps[0].result == "Hello world"
        assert pl.steps[0].tools == []
        assert pl.steps[0]._thinking_parts == []
        # And we still emit step_progress for both message.part chunks
        progress = [e for e in pl._event_history if e["event"] == "step_progress"]
        assert len(progress) == 2
        assert all(e["data"]["kind"] == "message.part" for e in progress)
