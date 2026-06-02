"""Unit tests for src/mesh.py — A2A Mesh L0 (Agent Card + peer table)."""

import os, sys, time, tempfile, textwrap
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from src.mesh import MeshManager, PeerInfo, DEFAULT_PRICING


_CONFIG = textwrap.dedent("""\
    agents:
      kiro:
        enabled: true
        description: "Kiro CLI agent"
        capabilities:
          domains: [devops, cloud]
          tools: [bash]
          languages: {shell: true}
          tags: [cli-first]
          version: "1.2.3"
      codex:
        enabled: true
        description: "Codex agent"
      disabled_one:
        enabled: false
        description: "should not appear"
""")


def _mgr(seeds=None, mesh_auth="", config_text=_CONFIG, agents_cfg=None):
    f = tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False)
    f.write(config_text); f.close()
    import yaml
    cfg = yaml.safe_load(config_text)
    return MeshManager(**{
        "node_name": "node-a",
        "self_url": "http://127.0.0.1:18010/",
        "version": "0.25.0",
        "agents_cfg": agents_cfg if agents_cfg is not None else cfg["agents"],
        "config_path": f.name,
        "seeds": seeds or [],
        "token": mesh_auth,
        "announce_interval": 300,
    })


# --- build_agent_card ------------------------------------------------------

def test_card_includes_only_enabled_agents():
    card = _mgr().build_agent_card()
    ids = {s["id"] for s in card["skills"]}
    assert ids == {"kiro", "codex"}            # disabled_one excluded
    assert card["url"] == "http://127.0.0.1:18010"  # trailing slash stripped
    assert card["version"] == "0.25.0"
    assert card["name"] == "acp-bridge@node-a"


def test_card_skill_pricing_is_free():
    card = _mgr().build_agent_card()
    for s in card["skills"]:
        assert s["pricing"] == DEFAULT_PRICING
        assert s["pricing"]["rate"] == 0


def test_card_skill_uses_capability_tags_and_version():
    card = _mgr().build_agent_card()
    kiro = next(s for s in card["skills"] if s["id"] == "kiro")
    assert set(kiro["tags"]) >= {"devops", "cloud", "cli-first"}
    assert kiro["version"] == "1.2.3"
    assert kiro["description"] == "Kiro CLI agent"


def test_card_minimal_skill_when_no_capabilities():
    card = _mgr().build_agent_card()
    codex = next(s for s in card["skills"] if s["id"] == "codex")
    assert codex["tags"] == []
    assert "version" not in codex
    assert codex["pricing"]["rate"] == 0


# --- record_peer / peer table ---------------------------------------------

def _peer_card(url, agents):
    return {"url": url, "skills": [
        {"id": a, "name": a, "pricing": {"model": "free", "rate": 0}} for a in agents]}


def test_record_peer_extracts_skills_and_pricing():
    m = _mgr()
    m.record_peer(_peer_card("http://127.0.0.1:18011", ["claude", "qwen"]))
    assert "http://127.0.0.1:18011" in m._peers
    p = m._peers["http://127.0.0.1:18011"]
    assert p.skills == ["claude", "qwen"]
    assert p.pricing == {"model": "free", "rate": 0}
    assert p.healthy is True


def test_record_peer_ignores_self():
    m = _mgr()
    m.record_peer({"url": "http://127.0.0.1:18010", "skills": []})
    assert m._peers == {}


def test_record_peer_strips_trailing_slash_and_dedups():
    m = _mgr()
    m.record_peer(_peer_card("http://127.0.0.1:18011/", ["claude"]))
    m.record_peer(_peer_card("http://127.0.0.1:18011", ["claude", "qwen"]))
    assert len(m._peers) == 1
    assert m._peers["http://127.0.0.1:18011"].skills == ["claude", "qwen"]


def test_gossip_adds_peers_of_peers_as_unhealthy():
    m = _mgr()
    m.record_peer(_peer_card("http://127.0.0.1:18011", ["claude"]),
                  peers=["http://127.0.0.1:18012", "http://127.0.0.1:18010"])
    # learned peer placeholder, self excluded
    assert m._peers["http://127.0.0.1:18012"].healthy is False
    assert "http://127.0.0.1:18010" not in m._peers


# --- mark_stale ------------------------------------------------------------

def test_mark_stale_flags_old_peers():
    m = _mgr()  # announce_interval=300 -> cutoff 900s
    m.record_peer(_peer_card("http://127.0.0.1:18011", ["claude"]))
    m._peers["http://127.0.0.1:18011"].last_seen = time.time() - 1000
    m.mark_stale()
    assert m._peers["http://127.0.0.1:18011"].healthy is False


def test_mark_stale_keeps_fresh_peers():
    m = _mgr()
    m.record_peer(_peer_card("http://127.0.0.1:18011", ["claude"]))
    m.mark_stale()
    assert m._peers["http://127.0.0.1:18011"].healthy is True


def test_known_peers_and_view():
    m = _mgr()
    m.record_peer(_peer_card("http://127.0.0.1:18011", ["claude"]))
    assert m.known_peers() == ["http://127.0.0.1:18011"]
    view = m.peers_view()
    assert view[0]["url"] == "http://127.0.0.1:18011"
    assert view[0]["skills"] == ["claude"]
