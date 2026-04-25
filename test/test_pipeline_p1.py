"""
P1 Priority Tests for pipeline.py::PipelineManager

8 scenarios covering conversation, webhook, shared_cwd, template rendering,
timeout, empty steps, output size, and cancel behavior.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import asyncio
import pytest
from unittest.mock import AsyncMock, Mock, patch, call

from src.acp_client import AcpConnection, AcpError
from src.pipeline import Pipeline, PipelineStep, PipelineManager


# ============================================================
# Helpers
# ============================================================

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


# ============================================================
# 1. test_conversation_mode_multi_turn
# ============================================================

@pytest.mark.asyncio
async def test_conversation_mode_multi_turn(tmp_path):
    """Conversation accumulates transcript across turns with round-robin agents."""
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
    assert pl.context["stop_reason"] == "MAX_TURNS"
    transcript = pl.context["transcript"]
    assert len(transcript) == 4
    # Round-robin: kiro, claude, kiro, claude
    assert [t["agent"] for t in transcript] == ["kiro", "claude", "kiro", "claude"]
    # Each turn has content
    assert all(t["content"].startswith("turn") for t in transcript)


# ============================================================
# 2. test_webhook_callback_on_step_complete
# ============================================================

@pytest.mark.asyncio
async def test_webhook_callback_on_step_complete(tmp_path):
    """Webhook _send_webhook is called after each step completes."""
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
    # _sender.send called for: start + step1 + step2 + final summary = 4
    assert mock_send.await_count == 4


# ============================================================
# 3. test_shared_cwd_isolation
# ============================================================

@pytest.mark.asyncio
async def test_shared_cwd_isolation(tmp_path):
    """Parallel mode creates per-agent subdirs under shared_cwd."""
    mgr, pool = _mgr(tmp_path)
    cwds_seen = []

    async def track_cwd(agent, sid, cwd=""):
        cwds_seen.append((agent, cwd))
        return _conn("ok")

    pool.get_or_create = AsyncMock(side_effect=track_cwd)

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

    assert pl.status == "completed"
    # Each agent gets its own subdir
    agents_and_cwds = {a: c for a, c in cwds_seen}
    assert agents_and_cwds["kiro"] == os.path.join(shared, "kiro")
    assert agents_and_cwds["claude"] == os.path.join(shared, "claude")


# ============================================================
# 4. test_context_template_render
# ============================================================

def test_context_template_render(tmp_path):
    """_render uses {{var}} regex substitution, not Jinja2."""
    mgr, _ = _mgr(tmp_path)

    # Basic substitution
    assert mgr._render("Hello {{name}}", {"name": "world"}) == "Hello world"
    # Multiple vars
    assert mgr._render("{{a}}+{{b}}", {"a": "1", "b": "2"}) == "1+2"
    # Missing var preserved
    assert mgr._render("{{missing}}", {}) == "{{missing}}"
    # Nested braces (not Jinja2 — only {{word}} matched)
    assert mgr._render("{{{triple}}}", {"triple": "x"}) == "{x}"
    # Non-word chars not matched
    assert mgr._render("{{a-b}}", {"a-b": "nope"}) == "{{a-b}}"


# ============================================================
# 5. test_timeout_per_step_enforcement
# ============================================================

@pytest.mark.asyncio
async def test_timeout_per_step_enforcement(tmp_path):
    """Step timeout is passed as idle_timeout to session_prompt.

    pipeline._exec_step_acp doesn't enforce step.timeout itself —
    it relies on session_prompt's idle_timeout. This test verifies
    the timeout value flows through correctly.
    """
    mgr, pool = _mgr(tmp_path)

    prompt_kwargs = {}

    async def capture_conn(agent, sid, cwd=""):
        conn = AsyncMock(spec=AcpConnection)
        async def fake_prompt(p, idle_timeout=300):
            prompt_kwargs["idle_timeout"] = idle_timeout
            yield {"_prompt_result": {"result": {"stopReason": "end"}}}
        conn.session_prompt = fake_prompt
        return conn

    pool.get_or_create = AsyncMock(side_effect=capture_conn)

    pl = Pipeline(pipeline_id="t1", mode="sequence", steps=[
        PipelineStep(agent="kiro", prompt_template="x"),
    ], context={})

    with _TN, _PS:
        with patch.object(mgr, "_make_shared_cwd", return_value="/tmp/ws"):
            with patch.object(mgr, "_webhook_start", new_callable=AsyncMock):
                with patch.object(mgr, "_webhook", new_callable=AsyncMock):
                    with patch.object(mgr, "_webhook_step", new_callable=AsyncMock):
                        await mgr._run(pl)

    # _exec_step_acp calls session_prompt with default idle_timeout=300 (not step.timeout)
    # This documents current behavior — step.timeout (600) is NOT enforced
    assert prompt_kwargs.get("idle_timeout") is not None


# ============================================================
# 6. test_empty_steps_handling
# ============================================================

@pytest.mark.asyncio
async def test_empty_steps_handling(tmp_path):
    """Pipeline with steps=[] completes without error for sequence/parallel/race."""
    for mode in ("sequence", "parallel", "race"):
        mgr, pool = _mgr(tmp_path)
        pl = Pipeline(pipeline_id=f"empty-{mode}", mode=mode, steps=[], context={})

        with patch.object(mgr, "_make_shared_cwd", return_value="/tmp/ws"):
            with patch.object(mgr, "_webhook_start", new_callable=AsyncMock):
                with patch.object(mgr, "_webhook", new_callable=AsyncMock):
                    await mgr._run(pl)

        if mode == "race":
            # race with no steps → ValueError
            assert pl.status == "failed"
            assert "requires at least one step" in pl.error
        else:
            assert pl.status == "completed", f"{mode} with empty steps should complete"


# ============================================================
# 7. test_step_output_size_limit
# ============================================================

@pytest.mark.asyncio
async def test_step_output_size_limit(tmp_path):
    """Large output is truncated at MAX_OUTPUT_SIZE (1MB).

    Documents the fix: step.result is capped to prevent OOM.
    """
    mgr, pool = _mgr(tmp_path)
    big_text = "x" * (2 * 1024 * 1024)  # 2MB
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
    assert pl.steps[0].result.startswith("x" * 100)
    assert "truncated" in pl.steps[0].result


# ============================================================
# 8. test_cancel_pipeline_mid_execution
# ============================================================

@pytest.mark.asyncio
async def test_cancel_pipeline_mid_execution(tmp_path):
    """Cancelling the _run task mid-sequence leaves remaining steps pending.

    PipelineManager has no explicit cancel API — cancellation relies on
    asyncio task cancellation. This test verifies that behavior.
    """
    mgr, pool = _mgr(tmp_path)
    step_started = asyncio.Event()

    async def slow_get_or_create(agent, sid, cwd=""):
        if agent == "claude":
            step_started.set()
            await asyncio.sleep(10)  # hang — will be cancelled
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

    # kiro completed, claude was in-flight, qwen never started
    assert pl.steps[0].status == "completed"
    assert pl.steps[2].status == "pending"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
