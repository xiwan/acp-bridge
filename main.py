"""ACP Bridge — remote CLI agent gateway."""

import argparse
import asyncio
import logging
import os
import re
import sys
import time

import uvicorn
import yaml
from acp_sdk.server import Server
from acp_sdk.server.app import create_app

from src.acp_client import AcpProcessPool
from src.agents import make_acp_agent_handler, make_pty_agent_handler
from src.jobs import JobManager
from src.security import SecurityMiddleware

_VERSION = open(os.path.join(os.path.dirname(__file__), "VERSION")).read().strip()

log = logging.getLogger("acp-bridge")


def setup_logging(verbose: bool):
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format='{"ts":"%(asctime)s","level":"%(levelname)s","logger":"%(name)s","msg":"%(message)s"}',
    )
    if not verbose:
        logging.getLogger("uvicorn.access").setLevel(logging.WARNING)


def load_config(path: str) -> dict:
    with open(path) as f:
        raw = f.read()
    raw = re.sub(r"\$\{(\w+)\}", lambda m: os.environ.get(m.group(1), ""), raw)
    return yaml.safe_load(raw)


def main():
    parser = argparse.ArgumentParser(description="ACP Bridge Server")
    parser.add_argument("--host", help="Override listen host")
    parser.add_argument("--port", type=int, help="Override listen port")
    parser.add_argument("--config", default="config.yaml", help="Config file path")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    setup_logging(args.verbose)
    config = load_config(args.config)

    agents_cfg = {k: v for k, v in config.get("agents", {}).items() if v.get("enabled")}
    if not agents_cfg:
        log.error("No enabled agents in config")
        sys.exit(1)

    # LiteLLM dependency check
    litellm_cfg = config.get("litellm", {})
    litellm_url = litellm_cfg.get("url", "")
    required_by = litellm_cfg.get("required_by", [])
    if litellm_url and required_by:
        import httpx
        try:
            resp = httpx.get(f"{litellm_url}/health", timeout=5)
            resp.raise_for_status()
            log.info("litellm: reachable at %s", litellm_url)
            litellm_env = litellm_cfg.get("env", {})
            for name in required_by:
                if name in agents_cfg:
                    agents_cfg[name].setdefault("env", {}).update(litellm_env)
        except Exception as e:
            log.warning("litellm: unreachable at %s (%s)", litellm_url, e)
            disabled = [n for n in required_by if n in agents_cfg]
            for name in disabled:
                del agents_cfg[name]
            if disabled:
                print(f"\n⚠️  LiteLLM ({litellm_url}) is not reachable — disabled agents: {', '.join(disabled)}\n")
            if not agents_cfg:
                log.error("All agents disabled due to litellm dependency")
                sys.exit(1)

    # Pool for ACP mode agents
    pool_cfg = config.get("pool", {})
    acp_agents = {k: v for k, v in agents_cfg.items() if v.get("mode") == "acp"}
    pool = AcpProcessPool(
        agents_config=acp_agents,
        max_processes=pool_cfg.get("max_processes", 20),
        max_per_agent=pool_cfg.get("max_per_agent", 10),
        verbose=args.verbose,
    ) if acp_agents else None

    # Register agents
    server = Server()
    for name, cfg in agents_cfg.items():
        mode = cfg.get("mode", "pty")
        if mode == "acp" and pool:
            handler = make_acp_agent_handler(name, pool)
        else:
            handler = make_pty_agent_handler(cfg, verbose=args.verbose)
        server.agent(name=name, description=cfg.get("description", ""))(handler)
        log.info("registered: agent=%s mode=%s cmd=%s", name, mode, cfg.get("command"))

    # Security
    sec_cfg = config.get("security", {})
    allowed_ips = sec_cfg.get("allowed_ips", [])
    auth_token = sec_cfg.get("auth_token", "")

    # Server config
    srv_cfg = config.get("server", {})
    host = args.host or srv_cfg.get("host", "0.0.0.0")
    port = args.port or srv_cfg.get("port", 8001)
    ttl_hours = srv_cfg.get("session_ttl_hours", 24)
    shutdown_timeout = srv_cfg.get("shutdown_timeout", 30)

    app = create_app(*server.agents)
    app.add_middleware(SecurityMiddleware, allowed_ips=allowed_ips, auth_token=auth_token)

    from contextlib import asynccontextmanager
    from fastapi import Path as PathParam
    from fastapi.responses import JSONResponse
    from pydantic import BaseModel

    start_time = time.time()

    # Job manager for async mode
    webhook_cfg = config.get("webhook", {})
    pty_agents = {k: v for k, v in agents_cfg.items() if v.get("mode") != "acp"}
    job_mgr = JobManager(
        pool=pool,
        pty_configs=pty_agents,
        webhook_url=webhook_cfg.get("url", ""),
        webhook_token=webhook_cfg.get("token", ""),
    ) if (pool or pty_agents) else None

    class JobRequest(BaseModel):
        agent_name: str
        session_id: str = ""
        prompt: str
        callback_url: str = ""
        callback_meta: dict = {}
        discord_target: str = ""

    @app.post("/jobs")
    async def submit_job(req: JobRequest):
        if not job_mgr:
            return JSONResponse({"error": "no pool configured"}, status_code=500)
        import uuid as _uuid
        sid = req.session_id or str(_uuid.uuid4())
        meta = req.callback_meta
        if req.discord_target:
            meta["discord_target"] = req.discord_target
        job = job_mgr.submit(req.agent_name, sid, req.prompt,
                             req.callback_url, meta)
        return {"job_id": job.job_id, "status": job.status, "agent": job.agent, "session_id": sid}

    @app.get("/jobs/{job_id}")
    async def get_job(job_id: str = PathParam(...)):
        if not job_mgr:
            return JSONResponse({"error": "no pool configured"}, status_code=500)
        job = job_mgr.get(job_id)
        if not job:
            return JSONResponse({"error": "job not found"}, status_code=404)
        return job.to_dict()

    @app.get("/jobs")
    async def list_jobs():
        if not job_mgr:
            return {"jobs": []}
        jobs = job_mgr.list_jobs()
        return {
            "jobs": [j.to_dict() for j in jobs],
            "summary": {
                "pending": sum(1 for j in jobs if j.status == "pending"),
                "running": sum(1 for j in jobs if j.status == "running"),
                "completed": sum(1 for j in jobs if j.status == "completed"),
                "failed": sum(1 for j in jobs if j.status == "failed"),
            },
        }

    @app.get("/health")
    async def health():
        return {"status": "ok", "version": _VERSION, "uptime": int(time.time() - start_time)}

    @app.get("/health/agents")
    async def health_agents():
        stats = pool.stats if pool else {"by_agent": {}}
        agent_list = []
        for name, cfg in agents_cfg.items():
            alive = stats["by_agent"].get(name, 0)
            # Check if agent processes are actually responsive
            healthy = True
            if pool:
                for (a, sid), conn in pool._connections.items():
                    if a == name and not conn.alive:
                        healthy = False
                        break
            agent_list.append({
                "name": name,
                "mode": cfg.get("mode", "pty"),
                "alive_sessions": alive,
                "healthy": healthy,
            })
        return {"version": _VERSION, "agents": agent_list}

    if pool:
        @app.delete("/sessions/{agent}/{session_id}")
        async def delete_session(agent: str = PathParam(...), session_id: str = PathParam(...)):
            await pool.close(agent, session_id)
            return {"status": "closed", "agent": agent, "session_id": session_id}

    async def cleanup_loop():
        while True:
            await asyncio.sleep(60)
            if pool:
                await pool.cleanup_idle(ttl_hours * 3600)
            if job_mgr:
                job_mgr.cleanup()

    _original_lifespan = app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(application):
        async with _original_lifespan(application):
            task = asyncio.create_task(cleanup_loop())
            yield
            task.cancel()
            if pool:
                log.info("shutting down, killing all subprocesses...")
                await pool.shutdown()

    app.router.lifespan_context = lifespan

    log.info("allowed_ips=%s", allowed_ips)
    if pool:
        log.info("pool: max=%d max_per_agent=%d", pool_cfg.get("max_processes", 20), pool_cfg.get("max_per_agent", 10))
    log.info("auth_token=%s", auth_token[:8] + "..." if len(auth_token) > 8 else auth_token)
    if job_mgr:
        log.info("jobs: monitor=60s stuck_timeout=600s webhook=%s", webhook_cfg.get("url", "(none)"))
    log.info("starting on %s:%s", host, port)

    # Print banner
    banner = (
        "\n"
        "╔══════════════════════════════════════════════════════════════╗\n"
        "║                                                              ║\n"
        "║     _   ___ ___   ___      _    _                            ║\n"
        "║    /_\\ / __| _ \\ | _ )_ __(_)__| |__ _  ___                  ║\n"
        "║   / _ \\ (__| _/  | _ \\ '_|| / _` / _` |/ -_)                 ║\n"
        "║  /_/ \\_\\___|_|   |___/|_| |_\\__,_\\__, \\___|                  ║\n"
        "║                                   |___/                      ║\n"
        "╠══════════════════════════════════════════════════════════════╣\n"
        "║                                                              ║\n"
        "║    🤖 Kiro ───┐                                              ║\n"
        "║                ├──► acp 🌉 ──► 🦞 OpenClaw ──► 🌍 world     ║\n"
        "║    🤖 Claude ──┘                                             ║\n"
        "║                                                              ║\n"
       f"║          v{_VERSION}  http://{host}:{port}                    ║\n"
        "╚══════════════════════════════════════════════════════════════╝\n"
    )
    print(banner)

    # Safety net: kill all agent process groups on exit, even if lifespan doesn't run
    import atexit, signal as _sig
    def _kill_all():
        if pool:
            for (a, s), conn in list(pool._connections.items()):
                try:
                    os.killpg(conn.proc.pid, _sig.SIGKILL)
                except (ProcessLookupError, PermissionError, OSError):
                    pass
    atexit.register(_kill_all)

    uvicorn.run(app, host=host, port=port, log_level="debug" if args.verbose else "info",
                timeout_graceful_shutdown=shutdown_timeout)


if __name__ == "__main__":
    main()
