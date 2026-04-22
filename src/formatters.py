"""IM channel formatters — format Job/Pipeline results for Discord / Feishu / fallback."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import yaml

if TYPE_CHECKING:
    from .jobs import Job

log = logging.getLogger("acp-bridge.formatters")

# ── Template Engine ──────────────────────────────────────

_TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
_cache: dict | None = None


def _load_templates() -> dict:
    global _cache
    if _cache is not None:
        return _cache
    path = _TEMPLATES_DIR / "default_formatter.yml"
    if path.exists():
        _cache = yaml.safe_load(path.read_text()) or {}
        log.info("loaded message templates from %s", path)
    else:
        _cache = {}
        log.warning("no template file at %s, using hardcoded defaults", path)
    return _cache


def reload_templates() -> None:
    """Force reload templates (e.g. after editing YAML)."""
    global _cache
    _cache = None
    _load_templates()


def get_template(section: str, key: str, default: str = "") -> str:
    """Get a single template string by section.key."""
    return _load_templates().get(section, {}).get(key, default)


def get_setting(key: str, default=None):
    """Get a value from the settings section."""
    return _load_templates().get("settings", {}).get(key, default)


def get_prompt_suffix() -> str:
    """Get the prompt suffix appended to all agent prompts."""
    return _load_templates().get("prompt_suffix", "")


def fmt(section: str, key: str, default: str = "", **kwargs) -> str:
    """Get template and format with kwargs. Missing vars left as-is."""
    tpl = get_template(section, key, default)
    try:
        return tpl.format(**kwargs)
    except KeyError:
        return tpl


# ── Pipeline Formatter ───────────────────────────────────

AGENT_ICONS = {"kiro": "🟢", "claude": "🟣", "codex": "🔵", "qwen": "🟠", "opencode": "⚪"}


def _quote(text: str) -> str:
    """Return text as-is (no quote wrapping)."""
    return text


def _preview(text: str) -> str:
    """Quote multi-line text. No truncation — content brevity is controlled by agent prompts."""
    return _quote(text.strip())


class PipelineFormatter:
    """Format pipeline webhook messages from YAML templates."""

    @staticmethod
    def format_start(pipeline_id: str, mode: str, agents: list[str]) -> str:
        flow = " → ".join(agents) if mode in ("sequence", "conversation") else " | ".join(agents)
        return fmt("pipeline", "start",
                    "🔗 **Pipeline** `{id}` started: {flow}",
                    id=pipeline_id[:8], flow=flow)

    @staticmethod
    def format_step(pipeline_id: str, step_idx: int, total: int,
                    agent: str, dur: float, status: str,
                    result: str = "", error: str = "") -> str:
        if status == "failed":
            return fmt("pipeline", "step_fail",
                       "🔗 `{id}` ❌ Step {idx}/{total}: **{agent}** ({dur}s)\n{error}",
                       id=pipeline_id[:8], idx=step_idx, total=total,
                       agent=agent, dur=dur, error=_quote(error))
        return fmt("pipeline", "step_ok",
                   "🔗 `{id}` ✅ Step {idx}/{total}: **{agent}** ({dur}s)\n{preview}",
                   id=pipeline_id[:8], idx=step_idx, total=total,
                   agent=agent, dur=dur, preview=_preview(result))

    @staticmethod
    def format_done(pipeline_id: str, status: str, dur: float,
                    error: str = "", steps: list | None = None) -> str:
        if status == "failed":
            header = fmt("pipeline", "done_fail",
                         "🔗 **Pipeline** `{id}` ❌ 失败，耗时 {dur}s\n{error}",
                         id=pipeline_id[:8], error=_quote(error), dur=dur)
        else:
            header = fmt("pipeline", "done_ok",
                         "🔗 **Pipeline** `{id}` ✅ 全部完成，耗时 {dur}s",
                         id=pipeline_id[:8], dur=dur)
        if steps:
            details = []
            for i, s in enumerate(steps, 1):
                if s.get("completed_at") and s.get("started_at"):
                    sd = round(s["completed_at"] - s["started_at"], 1)
                    icon = "✅" if s["status"] == "completed" else "❌" if s["status"] == "failed" else "⏭️"
                    details.append(fmt("pipeline", "detail_line",
                                       "> {icon} {idx}. {agent}: {dur}s",
                                       icon=icon, idx=i, agent=s["agent"], dur=sd))
            if details:
                header += "\n" + "\n".join(details)
        return header

    @staticmethod
    def format_turn(pipeline_id: str, turn: int, agent: str,
                    content: str, dur: float) -> str:
        icon = AGENT_ICONS.get(agent, "🤖")
        return fmt("pipeline", "turn",
                   "🔗 `{id}` 💬 Turn {turn}: {icon} **{agent}** ({dur}s)\n{preview}",
                   id=pipeline_id[:8], turn=turn, icon=icon,
                   agent=agent, dur=dur, preview=_preview(content))


# ── Job Formatters ───────────────────────────────────────


def get_formatter(channel: str) -> JobFormatter:
    """Route to the appropriate formatter by channel name."""
    return _FORMATTERS.get(channel, FallbackFormatter())


def _collapse(text: str) -> tuple[str, str | None]:
    """Return (summary, full_text_or_None) based on collapse_threshold setting.

    - Short text (≤ threshold): returns (text, None)
    - Long text (> threshold): returns (first N lines + char count, full text)
    """
    threshold = get_setting("collapse_threshold", 800)
    if len(text) <= threshold:
        return text, None
    lines = text.splitlines()
    n = get_setting("summary_lines", 5)
    summary = "\n".join(lines[:n])
    summary += f"\n... (共 {len(text)} 字符)"
    return summary, text


def _split(text: str, limit: int = 1900) -> list[str]:
    """Split text into chunks at line boundaries, each <= limit chars."""
    chunks, cur = [], ""
    for line in text.split("\n"):
        if cur and len(cur) + len(line) + 1 > limit:
            chunks.append(cur)
            cur = line
        else:
            cur = f"{cur}\n{line}" if cur else line
    if cur:
        chunks.append(cur)
    return chunks


def _duration(job: Job) -> float:
    return round(job.completed_at - job.created_at, 1)


def _tools_md(job: Job, limit: int = 10) -> str:
    return "\n".join(f"✅ `{t}`" for t in job.tools[:limit])


def _upload_result_s3(job: Job) -> str | None:
    """Write job result to upload_dir, upload to S3, return presigned URL or None."""
    from src import s3
    if not s3.is_available():
        return None
    try:
        upload_dir = os.environ.get("ACP_UPLOAD_DIR", "/tmp/acp-uploads")
        os.makedirs(upload_dir, exist_ok=True)
        fname = f"{job.agent}-{job.job_id[:8]}.md"
        path = os.path.join(upload_dir, fname)
        with open(path, "w") as f:
            f.write(job.result)
        return s3.upload(path, fname)
    except Exception:
        return None


@dataclass
class JobFormatter:
    """Base formatter — subclasses override format()."""

    text_limit: int = 1900

    def format(self, job: Job, target: str, base_url: str = "") -> list[dict]:
        raise NotImplementedError


class DiscordFormatter(JobFormatter):
    """Discord: summary card + body chunks (2000 char limit). Long output collapsed via thread."""

    text_limit: int = 1900

    def format(self, job: Job, target: str, base_url: str = "") -> list[dict]:
        dur = _duration(job)
        payloads = []

        # 1) Summary
        if job.status == "failed":
            summary = fmt("job", "summary_fail", "📨 **{agent}** `{job_id}`\n> ❌ {error}",
                          agent=job.agent, job_id=job.job_id, error=job.error)
        else:
            summary = fmt("job", "summary_ok", "📨 **{agent}** `{job_id}` ✅ Completed in {dur}s",
                          agent=job.agent, job_id=job.job_id, dur=dur)
        if job.tools:
            tools_hdr = fmt("job", "tools_header", "🔧 **Tools**")
            tool_lines = "\n".join(
                fmt("job", "tools_line", "> ✅ `{tool}`", tool=t) for t in job.tools[:10])
            summary += f"\n> \n> {tools_hdr}\n{tool_lines}"
        summary += f"\n> \n> {fmt('job', 'footer', '⏱️ {dur}s', dur=dur)}"

        # 2) Body — collapse long output into thread
        if job.status == "completed" and job.result.strip():
            short, full = _collapse(job.result)
            if full is None:
                # Short output: inline
                payloads.append(self._msg(target, summary))
                header = fmt("job", "result_header", "📄 **Result** — {agent} `{job_id}`",
                             agent=job.agent, job_id=job.job_id[:8])
                payloads.append(self._msg(target, f"{header}\n{_quote(short)}"))
            else:
                # Long output: try S3 presigned URL, fallback to thread chunks
                s3_url = _upload_result_s3(job)
                if s3_url:
                    summary += f"\n\n📎 **完整输出**: [点击下载]({s3_url})"
                    payloads.append(self._msg(target, summary))
                else:
                    summary += "\n\n📄 完整输出见下方 thread"
                    payloads.append(self._msg(target, summary))
                    chunks = _split(full, self.text_limit - 100)
                    for i, chunk in enumerate(chunks):
                        if len(chunks) > 1:
                            header = fmt("job", "result_header_part",
                                         "📄 **Result** — {agent} `{job_id}` [{part}/{total}]",
                                         agent=job.agent, job_id=job.job_id[:8], part=i+1, total=len(chunks))
                        else:
                            header = fmt("job", "result_header", "📄 **Result** — {agent} `{job_id}`",
                                         agent=job.agent, job_id=job.job_id[:8])
                        msg = self._msg(target, f"{header}\n{_quote(chunk)}")
                        msg["thread_content"] = True
                        if i == 0:
                            msg["thread_name"] = f"📄 {job.agent} {job.job_id[:8]} — 完整输出"
                        payloads.append(msg)
        else:
            payloads.append(self._msg(target, summary))

        return payloads

    @staticmethod
    def _msg(target: str, message: str) -> dict:
        return {"tool": "message", "action": "send",
                "args": {"channel": "discord", "target": target, "message": message}}


class FeishuFormatter(JobFormatter):
    """Feishu: markdown with code fences to trigger card rendering in OpenClaw."""

    text_limit: int = 3900

    def format(self, job: Job, target: str, base_url: str = "") -> list[dict]:
        dur = _duration(job)
        payloads = []

        # 1) Summary
        if job.status == "failed":
            summary = fmt("job", "summary_fail", "📨 **{agent}** `{job_id}`\n> ❌ {error}",
                          agent=job.agent, job_id=job.job_id, error=job.error)
        else:
            summary = fmt("job", "summary_ok", "📨 **{agent}** `{job_id}` ✅ Completed in {dur}s",
                          agent=job.agent, job_id=job.job_id, dur=dur)
        if job.tools:
            summary += f"\n\n{fmt('job', 'tools_header', '🔧 **Tools**')}"
            summary += "\n```\n" + "\n".join(t for t in job.tools[:10]) + "\n```"
        summary += f"\n\n{fmt('job', 'footer', '⏱️ {dur}s', dur=dur)}"
        payloads.append(self._msg(target, summary))

        # 2) Body chunks
        if job.status == "completed" and job.result.strip():
            chunks = _split(job.result, self.text_limit)
            for i, chunk in enumerate(chunks):
                prefix = f"**[{i+1}/{len(chunks)}]**\n" if len(chunks) > 1 else ""
                payloads.append(self._msg(target, prefix + chunk))

        return payloads

    @staticmethod
    def _msg(target: str, message: str) -> dict:
        return {"tool": "message", "action": "send",
                "args": {"channel": "feishu", "target": target, "message": message}}


class FallbackFormatter(JobFormatter):
    """Generic quote-block format."""

    def format(self, job: Job, target: str, base_url: str = "") -> list[dict]:
        dur = _duration(job)
        if job.status == "failed":
            header = fmt("job", "summary_fail", "📨 **{agent}** `{job_id}`\n> ❌ {error}",
                         agent=job.agent, job_id=job.job_id, error=job.error)
        else:
            header = fmt("job", "summary_ok", "📨 **{agent}** `{job_id}` ✅ Completed in {dur}s",
                         agent=job.agent, job_id=job.job_id, dur=dur)

        body = ""
        if job.tools:
            body += "\n".join(fmt("job", "tools_line", "> ✅ `{tool}`", tool=t) for t in job.tools[:10])
            body += "\n>\n"
        if job.result:
            body += _quote(job.result)

        footer = fmt("job", "footer", "⏱️ {dur}s", dur=dur)

        full = header
        if body:
            full += "\n" + body
        full += f"\n\n{footer}"

        chunks = _split(full, self.text_limit)
        return [{"tool": "message", "action": "send",
                 "args": {"channel": "discord", "target": target, "message": c}}
                for c in chunks]


_FORMATTERS: dict[str, JobFormatter] = {
    "discord": DiscordFormatter(),
    "feishu": FeishuFormatter(),
    "lark": FeishuFormatter(),
}
