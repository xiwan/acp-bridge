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
from src.routes import jobs as jobs_routes, tools as tools_routes, health as health_routes, chat as chat_routes

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
    parser.add_argument("--ui", action="store_true", help="Enable Web UI at /ui")
    args = parser.parse_args()

    setup_logging(args.verbose)
    config = load_config(args.config)

    # --- Agents ---
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
            litellm_env = litellm_cfg.get("env", {})
            api_key = litellm_env.get("LITELLM_API_KEY", "")
            headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
            resp = httpx.get(f"{litellm_url}/health/liveliness", timeout=15, headers=headers)
            resp.raise_for_status()
            log.info("litellm: reachable at %s", litellm_url)
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

    # --- Process pool ---
    pool_cfg = config.get("pool", {})
    acp_agents = {k: v for k, v in agents_cfg.items() if v.get("mode") == "acp"}
    pool = AcpProcessPool(
        agents_config=acp_agents,
        max_processes=pool_cfg.get("max_processes", 20),
        max_per_agent=pool_cfg.get("max_per_agent", 10),
        verbose=args.verbose,
    ) if acp_agents else None

    # --- Register agent handlers ---
    server = Server()
    for name, cfg in agents_cfg.items():
        mode = cfg.get("mode", "pty")
        if mode == "acp" and pool:
            handler = make_acp_agent_handler(name, pool)
        else:
            handler = make_pty_agent_handler(cfg, verbose=args.verbose)
        server.agent(name=name, description=cfg.get("description", ""))(handler)
        log.info("registered: agent=%s mode=%s cmd=%s", name, mode, cfg.get("command"))

    # --- Config values ---
    sec_cfg = config.get("security", {})
    srv_cfg = config.get("server", {})
    webhook_cfg = config.get("webhook", {})

    host = args.host or srv_cfg.get("host", "0.0.0.0")
    port = args.port or srv_cfg.get("port", 18010)
    ttl_hours = srv_cfg.get("session_ttl_hours", 24)
    shutdown_timeout = srv_cfg.get("shutdown_timeout", 30)
    ui_enabled = args.ui or srv_cfg.get("ui", False)

    # --- App + middleware ---
    app = create_app(*server.agents)
    app.add_middleware(SecurityMiddleware,
                       allowed_ips=sec_cfg.get("allowed_ips", []),
                       auth_token=sec_cfg.get("auth_token", ""),
                       rate_limit=sec_cfg.get("rate_limit", 60),
                       rate_window=sec_cfg.get("rate_window", 60),
                       max_body=sec_cfg.get("max_body_bytes", 1 * 1024 * 1024))

    # --- Job manager ---
    pty_agents = {k: v for k, v in agents_cfg.items() if v.get("mode") != "acp"}
    base_url = srv_cfg.get("base_url", f"http://{host}:{port}")
    job_mgr = JobManager(
        pool=pool, pty_configs=pty_agents,
        webhook_url=webhook_cfg.get("url", ""),
        webhook_token=webhook_cfg.get("token", ""),
        base_url=base_url,
    ) if (pool or pty_agents) else None

    # --- Register routes ---
    start_time = time.time()
    webhook_account_id = webhook_cfg.get("account_id", "")
    webhook_default_target = webhook_cfg.get("target", webhook_cfg.get("discord_target", ""))
    openclaw_url = webhook_cfg.get("url", "")

    health_routes.register(app, _VERSION, start_time, agents_cfg, pool, ttl_hours)
    jobs_routes.register(app, job_mgr, webhook_account_id, webhook_default_target)
    tools_routes.register(app, openclaw_url, webhook_cfg.get("token", ""), webhook_account_id)
    if ui_enabled:
        chat_routes.register(app, config)

    # --- Lifespan ---
    from contextlib import asynccontextmanager

    async def cleanup_loop():
        while True:
            await asyncio.sleep(60)
            if pool:
                await pool.health_check()
                await pool.cleanup_idle(ttl_hours * 3600)
                pool.cleanup_ghosts()
            if job_mgr:
                job_mgr.cleanup()

    _original_lifespan = app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(application):
        async with _original_lifespan(application):
            if pool:
                pool.cleanup_ghosts()
            task = asyncio.create_task(cleanup_loop())
            if job_mgr:
                asyncio.create_task(job_mgr.run_recovery())
            yield
            task.cancel()
            if pool:
                log.info("shutting down, killing all subprocesses...")
                await pool.shutdown()

    app.router.lifespan_context = lifespan

    # --- Logging ---
    auth_token = sec_cfg.get("auth_token", "")
    log.info("allowed_ips=%s", sec_cfg.get("allowed_ips", []))
    if pool:
        log.info("pool: max=%d max_per_agent=%d", pool_cfg.get("max_processes", 20), pool_cfg.get("max_per_agent", 10))
    log.info("auth_token=%s", auth_token[:8] + "..." if len(auth_token) > 8 else auth_token)
    if job_mgr:
        log.info("jobs: monitor=60s stuck_timeout=600s webhook=%s", webhook_cfg.get("url", "(none)"))
    if openclaw_url:
        log.info("tools_proxy: openclaw=%s", openclaw_url.replace("/tools/invoke", ""))
    if ui_enabled:
        log.info("web_ui: enabled at /ui")
    log.info("starting on %s:%s", host, port)

    # Banner
    print(
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
        "║    🤖 Claude ──┼──► acp 🌉 ──► 🦞 OpenClaw ──► 🌍 world     ║\n"
        "║    🤖 Codex ──┘                                              ║\n"
        "║                                                              ║\n"
       f"║          v{_VERSION}  http://{host}:{port}                    ║\n"
        "╚══════════════════════════════════════════════════════════════╝\n"
    )

    # Safety net
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
                timeout_graceful_shutdown=shutdown_timeout, loop="asyncio")


if __name__ == "__main__":
    main()
