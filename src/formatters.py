"""IM channel formatters — format Job results for Discord / Feishu / fallback."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .jobs import Job


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

    def format(self, job: Job, target: str) -> list[dict]:
        raise NotImplementedError


class DiscordFormatter(JobFormatter):
    """Discord: summary card + body chunks (2000 char limit)."""

    text_limit: int = 1900

    def format(self, job: Job, target: str) -> list[dict]:
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

        # 2) Body chunks — wrapped in quote block with header
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

    def format(self, job: Job, target: str) -> list[dict]:
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

    def format(self, job: Job, target: str) -> list[dict]:
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
