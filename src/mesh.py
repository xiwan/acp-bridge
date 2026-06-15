"""A2A Mesh L0 — decentralized discovery.

Exposes this Bridge as an A2A node: builds an Agent Card from local agents,
announces to seed peers, maintains a peer table. No remote invocation (L1/L2).

Agent Card is hand-built to the A2A spec shape (no a2a-sdk: that SDK is a
protobuf reimpl pulling heavy google deps; L0 only needs the JSON shape).
See design/a2a-mesh-spec-v2.md.
"""
import asyncio
import logging
import re
import time
from dataclasses import dataclass, field
from ipaddress import ip_address, ip_network
from typing import Optional

import httpx

from src.capability_registry import CapabilityRegistry

log = logging.getLogger("acp-bridge.mesh")

DEFAULT_PRICING = {"model": "free", "rate": 0, "currency": "USD", "unit": "per_1m_tokens"}
VALID_MODES = ("private", "public", "dual")


def _extract_ip(url: str) -> str:
    """Extract IP from a URL like http://1.2.3.4:18010."""
    m = re.search(r"//([^:/]+)", url or "")
    return m.group(1) if m else ""


def same_private_subnet(url_a: str, url_b: str, prefix_len: int = 16) -> bool:
    """Check if two URLs share same private subnet (first prefix_len bits)."""
    ip_a, ip_b = _extract_ip(url_a), _extract_ip(url_b)
    if not ip_a or not ip_b:
        return False
    try:
        a, b = ip_address(ip_a), ip_address(ip_b)
        if not a.is_private or not b.is_private:
            return False
        net = ip_network(f"{ip_a}/{prefix_len}", strict=False)
        return b in net
    except ValueError:
        return False


def select_peer_url(my_mode: str, my_private_url: str,
                    peer_url: str, peer_private_url: str, peer_public_url: str) -> str:
    """Select the best URL to reach a peer based on network mode."""
    if my_mode == "private":
        return peer_private_url or peer_url
    elif my_mode == "public":
        return peer_public_url or peer_url
    else:  # dual
        if peer_private_url and my_private_url:
            if same_private_subnet(my_private_url, peer_private_url):
                return peer_private_url
        return peer_public_url or peer_url


@dataclass
class PeerInfo:
    url: str
    agent_card: dict
    skills: list           # agent names (ids) extracted from the card
    pricing: dict          # reserved: peer's declared pricing (L0 stores, doesn't use)
    last_seen: float
    healthy: bool = True
    node_name: str = ""    # peer node name, parsed from card "acp-bridge@<node>"
    skill_info: dict = field(default_factory=dict)  # id -> full skill obj (desc/tags)
    private_url: str = ""  # from extensions.private_url
    public_url: str = ""   # from extensions.public_url
    mesh_mode: str = ""    # from extensions.mesh_mode


def _node_name_from_card(card: dict) -> str:
    """Extract '<node>' from an Agent Card name like 'acp-bridge@<node>'."""
    name = (card.get("name") or "") if isinstance(card, dict) else ""
    return name.split("@", 1)[1] if "@" in name else name


class MeshManager:
    """Owns the peer table and Agent Card construction for one Bridge node."""

    def __init__(self, *, node_name: str, self_url: str, version: str,
                 agents_cfg: dict, config_path: str,
                 seeds: list, token: str, announce_interval: int = 300,
                 max_hops: int = 1, pricing: Optional[dict] = None,
                 mode: str = "", private_url: str = "", public_url: str = ""):
        self.node_name = node_name
        self.version = version
        self.agents_cfg = agents_cfg
        self.seeds = [s.rstrip("/") for s in (seeds or [])]
        self.token = token
        self.announce_interval = announce_interval
        self.max_hops = max_hops
        self.pricing = pricing or dict(DEFAULT_PRICING)
        self._peers: dict[str, PeerInfo] = {}
        self._registry = CapabilityRegistry()
        try:
            self._registry.load(config_path)
        except Exception as e:  # config without capabilities is fine
            log.warning("mesh: capability load failed (%s); minimal skills only", e)
        self.on_cycle = None  # L2: optional hook run after each announce cycle

        # Network mode
        self.mode = mode if mode in VALID_MODES else "public"
        self.private_url = (private_url or "").rstrip("/")
        self.public_url = (public_url or "").rstrip("/")

        # Resolve self_url based on mode
        if self.mode == "private":
            self.self_url = self.private_url or self_url.rstrip("/")
        elif self.mode == "public":
            self.self_url = self.public_url or self_url.rstrip("/")
        else:  # dual — primary advertised url is public
            self.self_url = self.public_url or self_url.rstrip("/")

        # All addresses that identify "self" (for dedup)
        self._self_urls: set[str] = {u for u in [
            self.self_url, self.private_url, self.public_url, self_url.rstrip("/")
        ] if u}

        log.info("mesh: mode=%s self_url=%s private=%s public=%s",
                 self.mode, self.self_url, self.private_url, self.public_url)

    # --- Agent Card ---------------------------------------------------------

    def _agent_names(self) -> list:
        return [k for k, v in self.agents_cfg.items()
                if isinstance(v, dict) and v.get("enabled", True)]

    def _skill(self, name: str) -> dict:
        cap = self._registry.get_agent(name)
        tags = sorted(set((cap.domains if cap else []) + (cap.tags if cap else [])))
        skill = {
            "id": name,
            "name": name,
            "description": (self.agents_cfg.get(name, {}) or {}).get("description", f"{name} agent"),
            "tags": tags,
            "pricing": dict(self.pricing),  # billing reservation (free in L0)
        }
        if cap and cap.version:
            skill["version"] = cap.version
        return skill

    def build_agent_card(self) -> dict:
        card = {
            "name": f"acp-bridge@{self.node_name}",
            "description": "ACP Bridge mesh node",
            "url": self.self_url,
            "version": self.version,
            "capabilities": {"streaming": True},
            "defaultInputModes": ["text/plain"],
            "defaultOutputModes": ["text/plain"],
            "skills": [self._skill(n) for n in self._agent_names()],
        }
        # Extensions: advertise network mode + dual addresses
        extensions = {"mesh_mode": self.mode}
        if self.private_url:
            extensions["private_url"] = self.private_url
        if self.public_url:
            extensions["public_url"] = self.public_url
        card["extensions"] = extensions
        return card

    # --- Peer table ---------------------------------------------------------

    def _is_self(self, url: str) -> bool:
        return url.rstrip("/") in self._self_urls

    def record_peer(self, card: dict, peers: Optional[list] = None) -> None:
        url = (card.get("url") or "").rstrip("/")
        if not url or self._is_self(url):
            return
        # Also check extensions URLs for self-detection
        ext = card.get("extensions") or {}
        priv = (ext.get("private_url") or "").rstrip("/")
        pub = (ext.get("public_url") or "").rstrip("/")
        if priv and priv in self._self_urls:
            return
        if pub and pub in self._self_urls:
            return

        card_skills = card.get("skills") or []
        skills = [s.get("id") for s in card_skills if s.get("id")]
        skill_info = {s["id"]: s for s in card_skills if s.get("id")}
        pricing = card_skills[0].get("pricing", {}) if card_skills else {}
        self._peers[url] = PeerInfo(
            url, card, skills, pricing, time.time(),
            healthy=True, node_name=_node_name_from_card(card),
            skill_info=skill_info,
            private_url=priv, public_url=pub,
            mesh_mode=ext.get("mesh_mode", ""),
        )
        # gossip: learn peers-of-peers as placeholders (unhealthy until first contact)
        for p in (peers or []):
            p = (p or "").rstrip("/")
            if p and not self._is_self(p) and p not in self._peers:
                self._peers[p] = PeerInfo(p, {}, [], {}, time.time(), healthy=False)

    def known_peers(self) -> list:
        return list(self._peers.keys())

    def peers_view(self) -> list:
        return [{"url": p.url, "skills": p.skills, "healthy": p.healthy,
                 "last_seen": p.last_seen, "pricing": p.pricing,
                 "mesh_mode": p.mesh_mode,
                 "private_url": p.private_url, "public_url": p.public_url}
                for p in self._peers.values()]

    def resolve_peer_url(self, peer_url: str) -> str:
        """Given a peer's primary URL, return the best reachable address."""
        peer = self._peers.get(peer_url)
        if not peer:
            return peer_url
        return select_peer_url(self.mode, self.private_url,
                               peer.url, peer.private_url, peer.public_url)

    def mark_stale(self) -> None:
        cutoff = time.time() - self.announce_interval * 3
        for p in self._peers.values():
            if p.last_seen < cutoff:
                p.healthy = False

    # --- Announce -----------------------------------------------------------

    async def _announce_one(self, target: str) -> None:
        headers = {"Authorization": f"Bearer {self.token}"} if self.token else {}
        payload = {"agent_card": self.build_agent_card(),
                   "peers": self.known_peers(), "timestamp": time.time()}
        try:
            async with httpx.AsyncClient(timeout=5) as c:
                r = await c.post(f"{target}/a2a/announce", json=payload, headers=headers)
                r.raise_for_status()
                data = r.json()
                self.record_peer(data.get("agent_card", {}), data.get("peers", []))
        except Exception as e:
            log.warning("mesh: announce->%s failed: %s", target, e)

    async def announce_to_seeds(self) -> None:
        targets = set(self.seeds) | {u for u in self.known_peers()}
        targets -= self._self_urls
        await asyncio.gather(*(self._announce_one(t) for t in targets))

    async def announce_loop(self) -> None:
        while True:
            await self.announce_to_seeds()
            self.mark_stale()
            if self.on_cycle:
                try:
                    self.on_cycle()
                except Exception as e:
                    log.warning("mesh: on_cycle hook failed: %s", e)
            await asyncio.sleep(self.announce_interval)
