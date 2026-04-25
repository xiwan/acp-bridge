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
            with patch('src.pipeline.asyncio.create_task'):
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
            with patch('src.pipeline.asyncio.create_task'):
                pl = manager.submit("sequence", [{"agent": "kiro", "prompt": "x"}])
        assert manager.get(pl.pipeline_id) is not None

    def test_get_nonexistent_returns_none(self, manager):
        """get() returns None for unknown pipeline_id."""
        assert manager.get("nonexistent-id") is None

    def test_submit_respects_timeout(self, manager):
        """Step timeout from input dict is preserved."""
        with patch.object(manager, '_run', new_callable=AsyncMock):
            with patch('src.pipeline.asyncio.create_task'):
                pl = manager.submit("sequence", [
                    {"agent": "kiro", "prompt": "x", "timeout": 120},
                ])
        assert pl.steps[0].timeout == 120

    def test_submit_respects_output_as(self, manager):
        """output_as from input dict is preserved on step."""
        with patch.object(manager, '_run', new_callable=AsyncMock):
            with patch('src.pipeline.asyncio.create_task'):
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
        ], context={})

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
