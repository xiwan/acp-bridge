"""Dynamic harness endpoints — runtime creation of harness-factory agents."""

import logging
import time
import uuid

from fastapi import Request
from fastapi.responses import JSONResponse

from ..acp_client import AcpProcessPool
from ..agents import make_acp_agent_handler

log = logging.getLogger("acp-bridge.harness")

# In-memory registry: agent_name -> harness info
_registry: dict[str, dict] = {}

# Preset names matching harness-factory bundled profiles
PRESETS = {
    "reader":     "File reader (fs read-only)",
    "executor":   "Command executor (shell only)",
    "scout":      "Web scout (network only)",
    "reviewer":   "Code reviewer (fs + git)",
    "analyst":    "Data analyst (fs + shell)",
    "researcher": "Researcher (fs + web)",
    "developer":  "Software developer (fs + git + shell)",
    "writer":     "Technical writer (fs + git + web)",
    "operator":   "Operations engineer (fs + shell + web)",
    "admin":      "Full admin (all tools)",
}


def register(app, pool: AcpProcessPool | None, static_agents: dict, litellm_cfg: dict, harness_binary: str = ""):

    # Resolve harness-factory binary: explicit config > static agent > bare name
    _binary = harness_binary
    if not _binary:
        for cfg in static_agents.values():
            if cfg.get("command", "").endswith("harness-factory"):
                _binary = cfg["command"]
                break
    _binary = _binary or "harness-factory"

    harness_base_cfg = None
    for cfg in static_agents.values():
        if cfg.get("command", "").endswith("harness-factory"):
            harness_base_cfg = cfg
            break

    @app.get("/harness/presets")
    async def list_presets():
        return {"presets": PRESETS}

    @app.post("/harness")
    async def create_harness(request: Request):
        if not pool:
            return JSONResponse({"error": "no ACP process pool"}, status_code=503)

        body = await request.json()
        raw_profile = body.get("profile")
        if not raw_profile:
            return JSONResponse({"error": "profile is required (preset name or JSON object)"}, status_code=400)

        name = body.get("name") or f"harness-{uuid.uuid4().hex[:8]}"
        if name in static_agents or name in _registry:
            return JSONResponse({"error": f"agent '{name}' already exists"}, status_code=409)

        # Resolve profile: string → preset name, dict → custom profile
        if isinstance(raw_profile, str):
            if raw_profile not in PRESETS:
                return JSONResponse(
                    {"error": f"unknown preset '{raw_profile}'. Available: {', '.join(sorted(PRESETS))}"},
                    status_code=400)
            # Preset mode: pass --profile flag to harness-factory
            # Inherit agent config (model, temperature) from static harness so LLM calls work
            preset_name = raw_profile
            base_profile = harness_base_cfg.get("profile", {}) if harness_base_cfg else {}
            profile = {}
            if base_profile.get("agent"):
                profile["agent"] = {k: v for k, v in base_profile["agent"].items()
                                    if k in ("model", "temperature")}
            extra_acp_args = ["--profile", preset_name]
            description = body.get("description", PRESETS[preset_name])
        elif isinstance(raw_profile, dict):
            # Custom mode: pass full profile JSON via session/new
            preset_name = None
            profile = raw_profile
            extra_acp_args = []
            description = body.get("description", f"Dynamic harness: {name}")
        else:
            return JSONResponse({"error": "profile must be a preset name (string) or JSON object"}, status_code=400)

        # Inject litellm config
        profile.setdefault("litellm_url", litellm_cfg.get("url", ""))
        litellm_key = litellm_cfg.get("env", {}).get("LITELLM_API_KEY", "")
        profile.setdefault("litellm_api_key", litellm_key)

        # Override system_prompt if provided at top level
        if body.get("system_prompt"):
            profile.setdefault("agent", {})
            profile["agent"]["system_prompt"] = body["system_prompt"]

        # Build agent config
        base_args = harness_base_cfg.get("acp_args", []) if harness_base_cfg else []
        agent_cfg = {
            "command": _binary,
            "acp_args": base_args + extra_acp_args,
            "working_dir": f"/tmp/{name}",
            "mode": "acp",
            "profile": profile,
        }

        # Inject into process pool
        pool._config[name] = agent_cfg

        # Register into SDK agents dict
        handler = make_acp_agent_handler(name, pool, profile=profile)
        from acp_sdk.server import Server
        srv = Server()
        srv.agent(name=name, description=description)(handler)
        manifest = srv.agents[0]
        acp_agents = getattr(request.app.state, "acp_agents", None)
        if acp_agents is not None:
            acp_agents[manifest.name] = manifest

        created_at = time.time()
        _registry[name] = {
            "name": name,
            "description": description,
            "preset": preset_name,
            "profile": profile,
            "config": agent_cfg,
            "created_at": created_at,
        }

        log.info("harness_created: name=%s preset=%s", name, preset_name or "(custom)")
        return JSONResponse({
            "agent_name": name,
            "description": description,
            "preset": preset_name,
            "created_at": created_at,
        }, status_code=201)

    @app.get("/harness")
    async def list_harnesses():
        harnesses = []
        for name, info in _registry.items():
            sessions = sum(1 for (a, _) in pool._connections if a == name) if pool else 0
            # Pick any live connection's resolved_model (harness-factory 0.8.0+ reports it on session/new)
            model = None
            if pool:
                for (a, _), conn in pool._connections.items():
                    if a == name and getattr(conn, "resolved_model", None):
                        model = conn.resolved_model
                        break
            harnesses.append({
                "agent_name": name,
                "description": info["description"],
                "preset": info.get("preset"),
                "resolved_model": model,
                "created_at": info["created_at"],
                "active_sessions": sessions,
            })
        harness_used = sum(
            1 for (a, _) in pool._connections
            if pool._agent_group(a) == "harness"
        ) if pool else 0
        return {
            "harnesses": harnesses,
            "total": len(harnesses),
            "pool_usage": {
                "harness_slots_used": harness_used,
                "harness_slots_max": pool._max_per_agent if pool else 0,
            },
        }

    @app.delete("/harness/{agent_name}")
    async def delete_harness(agent_name: str, request: Request):
        if agent_name not in _registry:
            if agent_name in static_agents:
                return JSONResponse({"error": "cannot delete static agent"}, status_code=400)
            return JSONResponse({"error": "not found"}, status_code=404)

        killed = 0
        if pool:
            keys = [k for k in pool._connections if k[0] == agent_name]
            for key in keys:
                await pool._evict(key)
                killed += 1
            pool._config.pop(agent_name, None)

        acp_agents = getattr(request.app.state, "acp_agents", None)
        if acp_agents is not None:
            acp_agents.pop(agent_name, None)

        _registry.pop(agent_name)
        log.info("harness_deleted: name=%s sessions_killed=%d", agent_name, killed)
        return {"agent_name": agent_name, "deleted": True, "sessions_killed": killed}
