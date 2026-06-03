"""A2A Mesh L0 — decentralized discovery.

Exposes this Bridge as an A2A node: builds an Agent Card from local agents,
announces to seed peers, maintains a peer table. No remote invocation (L1/L2).

Agent Card is hand-built to the A2A spec shape (no a2a-sdk: that SDK is a
protobuf reimpl pulling heavy google deps; L0 only needs the JSON shape).
See design/a2a-mesh-spec-v2.md.
"""
import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Optional

import httpx

from src.capability_registry import CapabilityRegistry

log = logging.getLogger("acp-bridge.mesh")

DEFAULT_PRICING = {"model": "free", "rate": 0, "currency": "USD", "unit": "per_1m_tokens"}


@dataclass
class PeerInfo:
    url: str
    agent_card: dict
    skills: list           # agent names extracted from the card
    pricing: dict          # reserved: peer's declared pricing (L0 stores, doesn't use)
    last_seen: float
    healthy: bool = True


class MeshManager:
    """Owns the peer table and Agent Card construction for one Bridge node."""

    def __init__(self, *, node_name: str, self_url: str, version: str,
                 agents_cfg: dict, config_path: str,
                 seeds: list, token: str, announce_interval: int = 300,
                 max_hops: int = 1, pricing: Optional[dict] = None):
        self.node_name = node_name
        self.self_url = self_url.rstrip("/")
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
        return {
            "name": f"acp-bridge@{self.node_name}",
            "description": "ACP Bridge mesh node",
            "url": self.self_url,
            "version": self.version,
            "capabilities": {"streaming": True},
            "defaultInputModes": ["text/plain"],
            "defaultOutputModes": ["text/plain"],
            "skills": [self._skill(n) for n in self._agent_names()],
        }

    # --- Peer table ---------------------------------------------------------

    def record_peer(self, card: dict, peers: Optional[list] = None) -> None:
        url = (card.get("url") or "").rstrip("/")
        if not url or url == self.self_url:
            return
        skills = [s.get("id") for s in card.get("skills", []) if s.get("id")]
        pricing = (card.get("skills") or [{}])[0].get("pricing", {}) if card.get("skills") else {}
        self._peers[url] = PeerInfo(url, card, skills, pricing, time.time(), healthy=True)
        # gossip: learn peers-of-peers as placeholders (unhealthy until first contact)
        for p in (peers or []):
            p = (p or "").rstrip("/")
            if p and p != self.self_url and p not in self._peers:
                self._peers[p] = PeerInfo(p, {}, [], {}, time.time(), healthy=False)

    def known_peers(self) -> list:
        return list(self._peers.keys())

    def peers_view(self) -> list:
        return [{"url": p.url, "skills": p.skills, "healthy": p.healthy,
                 "last_seen": p.last_seen, "pricing": p.pricing}
                for p in self._peers.values()]

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
        targets.discard(self.self_url)
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
