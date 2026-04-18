"""IM channel formatters — format Job/Pipeline results for Discord / Feishu / fallback."""

from __future__ import annotations

import logging
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
    """Prefix every line with '> ' for markdown quote blocks."""
    return "\n".join(f"> {line}" for line in text.splitlines()) if text else ""


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


@dataclass
class JobFormatter:
    """Base formatter — subclasses override format()."""

    text_limit: int = 1900

    def format(self, job: Job, target: str, base_url: str = "") -> list[dict]:
        raise NotImplementedError


class DiscordFormatter(JobFormatter):
    """Discord: summary card + body chunks (2000 char limit)."""

    text_limit: int = 1900
    chunk_threshold: int = 2

    def format(self, job: Job, target: str, base_url: str = "") -> list[dict]:
        dur = _duration(job)
        payloads = []

        # 1) Summary
        summary = [f"📨 **ACP Bridge** — {job.agent} `{job.job_id}`"]
        if job.status == "failed":
            summary.append(f"> ❌ {job.error}")
        else:
            summary.append(f"> ✅ Completed in {dur}s")
        if job.tools:
            summary.append(f">\n> 🔧 **Tools**")
            for t in job.tools[:10]:
                summary.append(f"> ✅ `{t}`")
        summary.append(f">\n> ⏱️ {dur}s")
        payloads.append(self._msg(target, "\n".join(summary)))

        # 2) Body
        if job.status == "completed" and job.result.strip():
            chunks = _split(job.result, self.text_limit - 100)
            for i, chunk in enumerate(chunks):
                header = f"📄 **Result** — {job.agent} `{job.job_id[:8]}`"
                if len(chunks) > 1:
                    header += f" [{i+1}/{len(chunks)}]"
                body = "\n".join(f"> {line}" if line else ">" for line in chunk.splitlines())
                payloads.append(self._msg(target, f"{header}\n{body}"))

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

        # 1) Summary (use code fence to trigger card mode in OpenClaw)
        lines = [f"**🤖 {job.agent}** — `{job.job_id}`", ""]
        if job.status == "failed":
            lines.append(f"❌ {job.error}")
        else:
            lines.append(f"✅ Completed in {dur}s")
        if job.tools:
            lines.append("")
            lines.append("🔧 **Tools**")
            lines.append("```")
            lines.extend(t for t in job.tools[:10])
            lines.append("```")
        lines.append(f"\n⏱️ {dur}s")
        payloads.append(self._msg(target, "\n".join(lines)))

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
    """Generic quote-block format (original behavior)."""

    def format(self, job: Job, target: str, base_url: str = "") -> list[dict]:
        dur = _duration(job)
        header = f"📨 **ACP Bridge** — {job.agent} `{job.job_id}`\n>"

        if job.status == "failed":
            body = f"> ❌ {job.error}"
        else:
            lines = []
            if job.tools:
                for t in job.tools[:10]:
                    lines.append(f"> 🔧 `{t}`")
                lines.append(">")
            for ln in job.result.split("\n"):
                lines.append(f"> {ln}")
            body = "\n".join(lines)

        footer = f">\n📨 **Done** — {dur}s"

        chunks = _split(body, self.text_limit)
        payloads = []
        for i, chunk in enumerate(chunks):
            parts = []
            if i == 0:
                parts.append(header)
            if len(chunks) > 1:
                parts.append(f"> **[{i+1}/{len(chunks)}]**")
            parts.append(chunk)
            if i == len(chunks) - 1:
                parts.append(footer)
            payloads.append({
                "tool": "message", "action": "send",
                "args": {"channel": "discord", "target": target, "message": "\n".join(parts)},
            })
        return payloads


_FORMATTERS: dict[str, JobFormatter] = {
    "discord": DiscordFormatter(),
    "feishu": FeishuFormatter(),
    "lark": FeishuFormatter(),
}
