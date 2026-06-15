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


# --- mesh mode tests -------------------------------------------------------

from src.mesh import same_private_subnet, select_peer_url


def test_same_private_subnet_same_vpc():
    assert same_private_subnet("http://172.31.15.10:18010", "http://172.31.6.197:18010")


def test_same_private_subnet_different_vpc():
    assert not same_private_subnet("http://172.31.15.10:18010", "http://10.0.1.86:18010")


def test_same_private_subnet_public_ips():
    assert not same_private_subnet("http://34.213.151.41:18010", "http://44.228.130.244:18010")


def test_select_peer_url_dual_same_subnet():
    url = select_peer_url("dual", "http://172.31.15.10:18010",
                          "http://34.0.0.1:18010",
                          "http://172.31.6.197:18010", "http://34.0.0.1:18010")
    assert url == "http://172.31.6.197:18010"


def test_select_peer_url_dual_different_subnet():
    url = select_peer_url("dual", "http://172.31.15.10:18010",
                          "http://44.228.130.244:18010",
                          "http://10.0.1.86:18010", "http://44.228.130.244:18010")
    assert url == "http://44.228.130.244:18010"


def test_select_peer_url_public_mode():
    url = select_peer_url("public", "",
                          "http://44.0.0.1:18010",
                          "http://172.31.52.205:18010", "http://44.0.0.1:18010")
    assert url == "http://44.0.0.1:18010"


def test_select_peer_url_private_mode():
    url = select_peer_url("private", "http://172.31.15.10:18010",
                          "http://34.0.0.1:18010",
                          "http://172.31.6.197:18010", "http://34.0.0.1:18010")
    assert url == "http://172.31.6.197:18010"


def test_select_peer_url_backward_compat_no_extensions():
    url = select_peer_url("dual", "http://172.31.15.10:18010",
                          "http://172.31.52.205:18010", "", "")
    assert url == "http://172.31.52.205:18010"


def _mgr_dual():
    return _mgr.__wrapped__() if hasattr(_mgr, '__wrapped__') else MeshManager(**{
        "node_name": "node-a",
        "self_url": "http://34.213.151.41:18010",
        "version": "0.33.0",
        "agents_cfg": {"kiro": {"enabled": True, "description": "Kiro"}},
        "config_path": "/dev/null",
        "seeds": [],
        "token": "",
        "mode": "dual",
        "private_url": "http://172.31.15.10:18010",
        "public_url": "http://34.213.151.41:18010",
    })


def test_dual_mode_self_url_is_public():
    m = _mgr_dual()
    assert m.self_url == "http://34.213.151.41:18010"


def test_dual_mode_dedup_both_addresses():
    m = _mgr_dual()
    # Neither private nor public should be recorded as a peer
    m.record_peer({"url": "http://172.31.15.10:18010", "skills": []})
    m.record_peer({"url": "http://34.213.151.41:18010", "skills": []})
    assert m._peers == {}


def test_agent_card_has_extensions():
    m = _mgr_dual()
    card = m.build_agent_card()
    assert card["extensions"]["mesh_mode"] == "dual"
    assert card["extensions"]["private_url"] == "http://172.31.15.10:18010"
    assert card["extensions"]["public_url"] == "http://34.213.151.41:18010"


def test_record_peer_extracts_extensions():
    m = _mgr_dual()
    card = {
        "url": "http://44.228.130.244:18010",
        "skills": [{"id": "remote-kiro"}],
        "extensions": {
            "mesh_mode": "dual",
            "private_url": "http://172.31.52.205:18010",
            "public_url": "http://44.228.130.244:18010",
        }
    }
    m.record_peer(card)
    p = m._peers["http://44.228.130.244:18010"]
    assert p.private_url == "http://172.31.52.205:18010"
    assert p.public_url == "http://44.228.130.244:18010"
    assert p.mesh_mode == "dual"


def test_resolve_peer_url_dual_cross_vpc():
    m = _mgr_dual()
    card = {
        "url": "http://44.228.130.244:18010",
        "skills": [{"id": "remote-kiro"}],
        "extensions": {
            "mesh_mode": "dual",
            "private_url": "http://10.0.1.50:18010",
            "public_url": "http://44.228.130.244:18010",
        }
    }
    m.record_peer(card)
    # Different subnet (172.31 vs 10.0) → should use public
    assert m.resolve_peer_url("http://44.228.130.244:18010") == "http://44.228.130.244:18010"


def test_resolve_peer_url_dual_same_vpc():
    m = _mgr_dual()
    card = {
        "url": "http://34.0.0.2:18010",
        "skills": [{"id": "harness"}],
        "extensions": {
            "mesh_mode": "dual",
            "private_url": "http://172.31.6.197:18010",
            "public_url": "http://34.0.0.2:18010",
        }
    }
    m.record_peer(card)
    # Same subnet (172.31) → should use private
    assert m.resolve_peer_url("http://34.0.0.2:18010") == "http://172.31.6.197:18010"
