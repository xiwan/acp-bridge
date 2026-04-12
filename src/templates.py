"""Prompt template loader and renderer."""

import re
from pathlib import Path

import yaml

_TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "prompts" / "templates"
_VAR_RE = re.compile(r"\{\{(\w+)\}\}")


def _load_all() -> dict[str, dict]:
    templates = {}
    if not _TEMPLATES_DIR.exists():
        return templates
    for f in _TEMPLATES_DIR.glob("*.yaml"):
        try:
            t = yaml.safe_load(f.read_text())
            if t and t.get("name"):
                templates[t["name"]] = t
        except Exception:
            continue
    return templates


def list_templates() -> list[dict]:
    return [
        {"name": t["name"], "description": t.get("description", ""),
         "agent": t.get("agent", ""), "variables": _VAR_RE.findall(t.get("prompt", ""))}
        for t in _load_all().values()
    ]


def render(name: str, vars: dict[str, str] | None = None) -> dict:
    templates = _load_all()
    t = templates.get(name)
    if not t:
        return {"error": f"template not found: {name}"}
    prompt = t.get("prompt", "")
    vars = vars or {}
    # Replace {{var}} with provided values, leave unknown vars as-is
    prompt = _VAR_RE.sub(lambda m: vars.get(m.group(1), m.group(0)), prompt)
    return {"name": name, "agent": t.get("agent", ""), "prompt": prompt.strip()}
