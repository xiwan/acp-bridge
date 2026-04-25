"""
P0 Priority Tests for pipeline.py::PipelineManager

Covers the 8 P0 scenarios from pipeline-test-plan.md:
  1. sequence success + context chaining
  2. parallel success
  3. race first-wins
  4. _exec_step agent success
  5. _exec_step retry on failure (pipeline has no built-in retry — verifies single-attempt semantics)
  6. sequence stops on step failure
  7. parallel partial failure
  8. invalid mode raises error
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import asyncio
import pytest
from unittest.mock import AsyncMock, Mock, patch

from src.acp_client import AcpConnection, AcpError, PoolExhaustedError
from src.pipeline import Pipeline, PipelineStep, PipelineManager


# ============================================================
# Helpers
# ============================================================

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
    """Mock AcpConnection whose session_prompt yields a single message part."""
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


# ============================================================
# 1. test_submit_sequence_success
# ============================================================

@pytest.mark.asyncio
async def test_submit_sequence_success(tmp_path):
    """Scenario 1: sequence executes steps in order, chains context via output_as."""
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

    for p in _PATCHES:
        p.start()
    try:
        with patch.object(mgr, "_make_shared_cwd", return_value="/tmp/ws"):
            with patch.object(mgr, "_webhook_start", new_callable=AsyncMock):
                with patch.object(mgr, "_webhook", new_callable=AsyncMock):
                    with patch.object(mgr, "_webhook_step", new_callable=AsyncMock):
                        await mgr._run(pl)
    finally:
        for p in _PATCHES:
            p.stop()

    assert call_order == ["kiro", "claude"]
    assert pl.context["step1_out"] == "result_from_kiro"
    assert pl.status == "completed"
    assert pl.steps[-1].result == "result_from_claude"


# ============================================================
# 2. test_submit_parallel_success
# ============================================================

@pytest.mark.asyncio
async def test_submit_parallel_success(tmp_path):
    """Scenario 2: parallel runs all steps concurrently, all succeed."""
    mgr, pool = _make_manager(tmp_path)
    pool.get_or_create = AsyncMock(side_effect=lambda a, s, cwd="": _mock_conn(f"out_{a}"))

    pl = Pipeline(pipeline_id="p2", mode="parallel", steps=[
        PipelineStep(agent="kiro", prompt_template="t1"),
        PipelineStep(agent="claude", prompt_template="t2"),
        PipelineStep(agent="qwen", prompt_template="t3"),
    ], context={})

    for p in _PATCHES:
        p.start()
    try:
        with patch.object(mgr, "_make_shared_cwd", return_value="/tmp/ws"):
            with patch.object(mgr, "_webhook_start", new_callable=AsyncMock):
                with patch.object(mgr, "_webhook", new_callable=AsyncMock):
                    with patch.object(mgr, "_webhook_step", new_callable=AsyncMock):
                        await mgr._run(pl)
    finally:
        for p in _PATCHES:
            p.stop()

    assert pl.status == "completed"
    assert all(s.status == "completed" for s in pl.steps)
    assert pl.steps[0].result == "out_kiro"


# ============================================================
# 3. test_submit_race_first_wins
# ============================================================

@pytest.mark.asyncio
async def test_submit_race_first_wins(tmp_path):
    """Scenario 3: race mode — first completed step wins."""
    mgr, pool = _make_manager(tmp_path)

    async def speed_get_or_create(agent, sid, cwd=""):
        if agent == "qwen":
            await asyncio.sleep(0.05)  # slow
        return _mock_conn(f"win_{agent}")

    pool.get_or_create = AsyncMock(side_effect=speed_get_or_create)

    pl = Pipeline(pipeline_id="p3", mode="race", steps=[
        PipelineStep(agent="kiro", prompt_template="t"),
        PipelineStep(agent="qwen", prompt_template="t"),
    ], context={})

    for p in _PATCHES:
        p.start()
    try:
        with patch.object(mgr, "_make_shared_cwd", return_value="/tmp/ws"):
            with patch.object(mgr, "_webhook_start", new_callable=AsyncMock):
                with patch.object(mgr, "_webhook", new_callable=AsyncMock):
                    with patch.object(mgr, "_webhook_step", new_callable=AsyncMock):
                        await mgr._run(pl)
    finally:
        for p in _PATCHES:
            p.stop()

    assert pl.status == "completed"
    # At least one step completed
    completed = [s for s in pl.steps if s.status == "completed"]
    assert len(completed) >= 1


# ============================================================
# 4. test_exec_step_agent_success
# ============================================================

@pytest.mark.asyncio
async def test_exec_step_agent_success(tmp_path):
    """Scenario 4: _exec_step calls agent via pool, returns response, sets status."""
    mgr, pool = _make_manager(tmp_path)
    pool.get_or_create = AsyncMock(return_value=_mock_conn("agent_response"))

    pl = Pipeline(pipeline_id="p4", mode="sequence", steps=[], context={"shared_cwd": "/tmp/ws"})
    step = PipelineStep(agent="claude", prompt_template="test prompt")
    pl.steps.append(step)

    for p in _PATCHES:
        p.start()
    try:
        await mgr._exec_step(pl, step, "test prompt")
    finally:
        for p in _PATCHES:
            p.stop()

    assert step.status == "completed"
    assert step.result == "agent_response"
    assert step.completed_at > step.started_at
    pool.get_or_create.assert_awaited_once()


# ============================================================
# 5. test_exec_step_no_builtin_retry
# ============================================================

@pytest.mark.asyncio
async def test_exec_step_no_builtin_retry(tmp_path):
    """Scenario 5: _exec_step has no built-in retry — single failure → step failed."""
    mgr, pool = _make_manager(tmp_path)
    pool.get_or_create = AsyncMock(side_effect=AcpError("agent down"))

    pl = Pipeline(pipeline_id="p5", mode="sequence", steps=[], context={"shared_cwd": "/tmp/ws"})
    step = PipelineStep(agent="claude", prompt_template="x")
    pl.steps.append(step)

    for p in _PATCHES:
        p.start()
    try:
        await mgr._exec_step(pl, step, "x")
    finally:
        for p in _PATCHES:
            p.stop()

    assert step.status == "failed"
    assert "agent down" in step.error
    # Only one call — no retry
    assert pool.get_or_create.await_count == 1


# ============================================================
# 6. test_sequence_stops_on_step_failure
# ============================================================

@pytest.mark.asyncio
async def test_sequence_stops_on_step_failure(tmp_path):
    """Scenario 6: sequence aborts after first failure, remaining steps stay pending."""
    mgr, pool = _make_manager(tmp_path)

    async def fail_on_claude(agent, sid, cwd=""):
        if agent == "claude":
            raise AcpError("claude down")
        return _mock_conn("ok")

    pool.get_or_create = AsyncMock(side_effect=fail_on_claude)

    pl = Pipeline(pipeline_id="p6", mode="sequence", steps=[
        PipelineStep(agent="kiro", prompt_template="s1"),
        PipelineStep(agent="claude", prompt_template="s2"),
        PipelineStep(agent="qwen", prompt_template="s3"),
    ], context={})

    for p in _PATCHES:
        p.start()
    try:
        with patch.object(mgr, "_make_shared_cwd", return_value="/tmp/ws"):
            with patch.object(mgr, "_webhook_start", new_callable=AsyncMock):
                with patch.object(mgr, "_webhook", new_callable=AsyncMock):
                    with patch.object(mgr, "_webhook_step", new_callable=AsyncMock):
                        await mgr._run(pl)
    finally:
        for p in _PATCHES:
            p.stop()

    assert pl.status == "failed"
    assert pl.steps[0].status == "completed"
    assert pl.steps[1].status == "failed"
    assert pl.steps[2].status == "pending"  # never reached


# ============================================================
# 7. test_parallel_partial_failure
# ============================================================

@pytest.mark.asyncio
async def test_parallel_partial_failure(tmp_path):
    """Scenario 7: parallel — one step fails, others still complete."""
    mgr, pool = _make_manager(tmp_path)

    async def selective(agent, sid, cwd=""):
        if agent == "claude":
            raise AcpError("claude error")
        return _mock_conn(f"ok_{agent}")

    pool.get_or_create = AsyncMock(side_effect=selective)

    pl = Pipeline(pipeline_id="p7", mode="parallel", steps=[
        PipelineStep(agent="kiro", prompt_template="t1"),
        PipelineStep(agent="claude", prompt_template="t2"),
        PipelineStep(agent="qwen", prompt_template="t3"),
    ], context={})

    for p in _PATCHES:
        p.start()
    try:
        with patch.object(mgr, "_make_shared_cwd", return_value="/tmp/ws"):
            with patch.object(mgr, "_webhook_start", new_callable=AsyncMock):
                with patch.object(mgr, "_webhook", new_callable=AsyncMock):
                    with patch.object(mgr, "_webhook_step", new_callable=AsyncMock):
                        await mgr._run(pl)
    finally:
        for p in _PATCHES:
            p.stop()

    assert pl.status == "failed"
    assert pl.steps[0].status == "completed"
    assert pl.steps[1].status == "failed"
    assert "claude error" in pl.steps[1].error
    assert pl.steps[2].status == "completed"


# ============================================================
# 8. test_invalid_mode_raises_error
# ============================================================

@pytest.mark.asyncio
async def test_invalid_mode_raises_error(tmp_path):
    """Scenario 8: unknown mode → pipeline failed with error, no steps executed."""
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
    assert pl.steps[0].status == "pending"  # never executed


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
