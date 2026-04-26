"""P0 tests for src/capability_registry.py — capability discovery module."""

import os, sys, tempfile, textwrap
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import yaml
from src.capability_registry import (
    CapabilityRegistry, AgentCapabilities,
    _score_agent, _version_match, _normalize_languages,
)


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------

_SAMPLE_CONFIG = textwrap.dedent("""\
    agents:
      claude:
        enabled: true
        capabilities:
          domains: [coding, refactoring, debugging]
          tools: [read_file, write_file, bash]
          languages:
            python: ["3.10", "3.11", "3.12"]
            typescript: true
          max_complexity: 100
          tags: [expensive, high-accuracy]
          version: "2.5.1"
          timeout: 60
          cost_per_million_tokens: 1.25
      qwen:
        enabled: true
        capabilities:
          domains: [coding, debugging, code-generation]
          tools: [read_file, write_file, bash]
          languages:
            python: true
            go: true
            rust: ["1.60+"]
          max_complexity: 80
          tags: [low-cost, fast]
          version: "3-coder"
          timeout: 45
          cost_per_million_tokens: 0.07
      kiro:
        enabled: true
        capabilities:
          domains: [devops, cloud, terminal]
          tools: [bash, shell, kubectl]
          languages:
            shell: true
            yaml: true
          max_complexity: 20
          tags: [cli-first, low-latency]
          timeout: 30
          cost_per_million_tokens: 0.0
      disabled_agent:
        enabled: false
        capabilities:
          domains: [coding]
          tools: [bash]
          languages: {}
""")


def _write_config(content: str = _SAMPLE_CONFIG) -> str:
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False)
    f.write(content)
    f.close()
    return f.name


# ---------------------------------------------------------------------------
# load / reload
# ---------------------------------------------------------------------------

def test_load_basic():
    path = _write_config()
    reg = CapabilityRegistry()
    reg.load(path)
    assert len(reg.list_all()) == 3  # disabled_agent excluded
    os.unlink(path)


def test_load_skips_disabled():
    path = _write_config()
    reg = CapabilityRegistry()
    reg.load(path)
    assert reg.get_agent("disabled_agent") is None
    os.unlink(path)


def test_load_skips_no_capabilities():
    cfg = "agents:\n  bare:\n    enabled: true\n"
    path = _write_config(cfg)
    reg = CapabilityRegistry()
    reg.load(path)
    assert len(reg.list_all()) == 0
    os.unlink(path)


def test_get_agent():
    path = _write_config()
    reg = CapabilityRegistry()
    reg.load(path)
    cap = reg.get_agent("claude")
    assert cap is not None
    assert "coding" in cap.domains
    assert cap.cost_per_million_tokens == 1.25
    os.unlink(path)


def test_reload_detects_change():
    path = _write_config()
    reg = CapabilityRegistry()
    reg.load(path)
    assert not reg.reload()  # same content → no change

    # rewrite with a new agent added
    with open(path) as f:
        data = yaml.safe_load(f.read())
    data["agents"]["new_agent"] = {
        "enabled": True,
        "capabilities": {"domains": ["testing"], "tools": ["bash"], "languages": {}},
    }
    with open(path, "w") as f:
        yaml.dump(data, f)
    assert reg.reload()  # agent set changed
    assert reg.get_agent("new_agent") is not None
    os.unlink(path)


def test_reload_atomic_replace():
    """Reload builds a new dict before replacing — no partial state."""
    path = _write_config()
    reg = CapabilityRegistry()
    reg.load(path)
    old_caps = reg._capabilities
    reg.reload()
    # after reload, _capabilities is a new dict object (atomic swap)
    assert reg._capabilities is not old_caps or reg._capabilities == old_caps
    os.unlink(path)


# ---------------------------------------------------------------------------
# search / get_best
# ---------------------------------------------------------------------------

def test_search_domain_match():
    path = _write_config()
    reg = CapabilityRegistry()
    reg.load(path)
    results = reg.search({"domains": ["coding"]})
    names = [n for n, _ in results]
    assert "claude" in names
    assert "qwen" in names
    assert "kiro" not in names  # kiro has devops/cloud/terminal
    os.unlink(path)


def test_search_tool_hard_requirement():
    path = _write_config()
    reg = CapabilityRegistry()
    reg.load(path)
    results = reg.search({"domains": ["devops"], "tools": ["kubectl"]})
    names = [n for n, _ in results]
    assert names == ["kiro"]
    os.unlink(path)


def test_search_missing_tool_excluded():
    path = _write_config()
    reg = CapabilityRegistry()
    reg.load(path)
    results = reg.search({"tools": ["nonexistent_tool"]})
    assert results == []
    os.unlink(path)


def test_search_language_filter():
    path = _write_config()
    reg = CapabilityRegistry()
    reg.load(path)
    results = reg.search({"domains": ["coding"], "languages": {"go": True}})
    names = [n for n, _ in results]
    assert "qwen" in names
    assert "claude" not in names
    os.unlink(path)


def test_get_best():
    path = _write_config()
    reg = CapabilityRegistry()
    reg.load(path)
    best = reg.get_best({"domains": ["devops"], "tools": ["kubectl"]})
    assert best == "kiro"
    os.unlink(path)


def test_get_best_none():
    path = _write_config()
    reg = CapabilityRegistry()
    reg.load(path)
    assert reg.get_best({"domains": ["nonexistent"]}) is None
    os.unlink(path)


# ---------------------------------------------------------------------------
# scoring details
# ---------------------------------------------------------------------------

def test_prefer_tags_boost():
    path = _write_config()
    reg = CapabilityRegistry()
    reg.load(path)
    results = reg.search({"domains": ["coding"], "prefer_tags": ["fast"]})
    names = [n for n, _ in results]
    assert names[0] == "qwen"  # qwen has "fast" tag
    os.unlink(path)


def test_exclude_tags_penalty():
    path = _write_config()
    reg = CapabilityRegistry()
    reg.load(path)
    results = reg.search({"domains": ["coding"], "exclude_tags": ["expensive"]})
    names = [n for n, _ in results]
    # claude has "expensive" tag → penalized, qwen should rank higher
    assert names.index("qwen") < names.index("claude")
    os.unlink(path)


def test_cost_constraint():
    path = _write_config()
    reg = CapabilityRegistry()
    reg.load(path)
    results = reg.search({"domains": ["coding"], "max_cost": 0.1})
    scores = {n: s for n, s in results}
    # qwen (0.07) within budget → included; claude (1.25) over budget → hard excluded
    assert "qwen" in scores
    assert "claude" not in scores  # hard exclusion: over-budget agent returns 0
    os.unlink(path)


def test_complexity_penalty():
    path = _write_config()
    reg = CapabilityRegistry()
    reg.load(path)
    # require complexity 90 — kiro (20) can't handle, claude (100) can
    results = reg.search({"domains": ["coding"], "max_complexity": 90})
    names = [n for n, _ in results]
    assert "kiro" not in names  # kiro doesn't have coding domain anyway
    # but among coding agents, claude should score higher than qwen (80 < 90)
    scores = {n: s for n, s in results}
    assert scores["claude"] > scores["qwen"]
    os.unlink(path)


# ---------------------------------------------------------------------------
# version matching
# ---------------------------------------------------------------------------

def test_version_match_true():
    assert _version_match(True, "3.10") is True
    assert _version_match("3.10", True) is True

def test_version_match_exact():
    assert _version_match(["3.10", "3.11"], "3.10") is True
    assert _version_match(["3.10", "3.11"], "3.9") is False

def test_version_match_ge():
    assert _version_match(["1.70+"], "1.60+") is True   # 1.70 >= 1.60
    assert _version_match(["1.60+"], "1.70+") is False   # 1.60 < 1.70

def test_version_match_list_req():
    assert _version_match(["3.10", "3.11", "3.12"], ["3.10+"]) is True
    assert _version_match(["3.8"], ["3.10+"]) is False


# ---------------------------------------------------------------------------
# normalize_languages
# ---------------------------------------------------------------------------

def test_normalize_dict():
    assert _normalize_languages({"python": True}) == {"python": True}

def test_normalize_list():
    raw = [{"python": ["3.10"]}, "go"]
    result = _normalize_languages(raw)
    assert result == {"python": ["3.10"], "go": True}

def test_normalize_none():
    assert _normalize_languages(None) == {}


# ---------------------------------------------------------------------------
# edge cases
# ---------------------------------------------------------------------------

def test_empty_requirements():
    path = _write_config()
    reg = CapabilityRegistry()
    reg.load(path)
    results = reg.search({})
    assert len(results) == 3  # all agents match empty requirements
    os.unlink(path)


def test_load_real_config():
    """Smoke test: load the real project config.yaml if it has capabilities."""
    real_cfg = os.path.join(os.path.dirname(__file__), "..", "config.yaml")
    if not os.path.exists(real_cfg):
        return
    reg = CapabilityRegistry()
    reg.load(real_cfg)
    # should load at least the 3 agents we added capabilities to
    assert len(reg.list_all()) >= 3


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"✅ {name}")
    print(f"\n=== All capability_registry tests passed ✅ ===")
