"""ACP Bridge — remote CLI agent gateway."""

import argparse
import asyncio
import logging
import os
import re
import sys
import time
from pathlib import Path


def _load_dotenv(path: str = ".env") -> None:
    """Load .env file into os.environ (won't override existing vars)."""
    p = Path(__file__).resolve().parent / path
    if not p.exists():
        return
    for line in p.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())


_load_dotenv()

import uvicorn
import yaml
from acp_sdk.server import Server
from acp_sdk.server.app import create_app

from src.acp_client import AcpProcessPool
from src.agents import make_acp_agent_handler, make_pty_agent_handler, ping_loop
from src.jobs import JobManager
from src.security import SecurityMiddleware
from src.stats import StatsCollector
from src.routes import jobs as jobs_routes, tools as tools_routes, health as health_routes, chat as chat_routes, files as files_routes, pipelines as pipelines_routes, stats as stats_routes, templates as templates_routes, harness as harness_routes

try:
    from acp_sdk.models.models import Metadata
except ImportError:
    Metadata = None

_VERSION = open(os.path.join(os.path.dirname(__file__), "VERSION")).read().strip()

log = logging.getLogger("acp-bridge")


def setup_logging(verbose: bool):
    from src.trace import TraceIdFilter
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format='{"ts":"%(asctime)s","level":"%(levelname)s","trace":"%(trace_id)s","logger":"%(name)s","msg":"%(message)s"}',
    )
    # Attach filter to all handlers so every record gets trace_id, even from
    # third-party loggers that bypass our own logger tree.
    trace_filter = TraceIdFilter()
    for h in logging.getLogger().handlers:
        h.addFilter(trace_filter)
    if not verbose:
        logging.getLogger("uvicorn.access").setLevel(logging.WARNING)


def load_config(path: str) -> dict:
    with open(path) as f:
        raw = f.read()
    raw = re.sub(r"\$\{(\w+)\}", lambda m: os.environ.get(m.group(1), ""), raw)
    return yaml.safe_load(raw)


_BANNER = r"""
╔══════════════════════════════════════════════════════════════╗
║                                                              ║
║      _   ___ ___   ___      _    _                           ║
║     /_\ / __| _ \ | _ )_ __(_)__| |__ _  ___                ║
║    / _ \ (__| _/  | _ \ '_|| / _` / _` |/ -_)               ║
║   /_/ \_\___|_|   |___/|_| |_\__,_\__, \___|                ║
║                                    |___/                     ║
║          https://github.com/xiwan/acp-bridge                 ║
║                                                              ║
╠══════════════════════════════════════════════════════════════╣
║                                                              ║
║  IM Agents    🦞 OpenClaw  🐎 Hermes                         ║
║  CLI Agents   🤖 Claude Code  🤖 Kiro  🤖 Codex             ║
║               🤖 OpenCode  🤖 Qwen  ...                     ║
║  Lite Agents  🏭 Harness Agents                              ║
║                                                              ║
╚══════════════════════════════════════════════════════════════╝

    Multi-Agent Mesh · Connect · Orchestrate · Scale
"""


def main():
    print(_BANNER, flush=True)
    parser = argparse.ArgumentParser(description="ACP Bridge Server")
    parser.add_argument("--host", help="Override listen host")
    parser.add_argument("--port", type=int, help="Override listen port")
    parser.add_argument("--config", default="config.yaml", help="Config file path")
    parser.add_argument("--verbose", "-v", action="store_true")
    parser.add_argument("--ui", action="store_true", help="Enable Web UI at /ui")
    args = parser.parse_args()

    setup_logging(args.verbose)

    auto_mode = False
    if os.path.exists(args.config):
        config = load_config(args.config)
    else:
        from src.auto_detect import build_config
        config = build_config()
        auto_mode = True
        agents = config.get("agents", {})
        if not agents:
            log.error("No config.yaml found and no agent CLIs detected in PATH")
            sys.exit(1)
        token = config["security"]["auth_token"]
        print(f"\n⚡ Zero-config mode: detected {len(agents)} agent(s): {', '.join(agents)}")
        print(f"🔑 Auth token: {token}")
        print(f"   (set ACP_BRIDGE_TOKEN env to use a fixed token)\n")

    # --- Agents ---
    agents_cfg = {k: v for k, v in config.get("agents", {}).items() if v.get("enabled")}
    if not agents_cfg:
        log.error("No enabled agents in config")
        sys.exit(1)

    # Harness binary resolution
    harness_cfg = config.get("harness", {})
    harness_binary = harness_cfg.get("binary", "")
    if harness_binary:
        for cfg in agents_cfg.values():
            if cfg.get("command") == "harness-factory":
                cfg["command"] = harness_binary

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

    # Ensure all working dirs exist
    for cfg in agents_cfg.values():
        os.makedirs(cfg.get("working_dir", "/tmp"), exist_ok=True)
    pool = AcpProcessPool(
        agents_config=acp_agents,
        max_processes=pool_cfg.get("max_processes", 20),
        max_per_agent=pool_cfg.get("max_per_agent", 10),
        verbose=args.verbose,
    ) if acp_agents else None
    if pool:
        pool._memory_limit_pct = pool_cfg.get("memory_limit_percent", 80)

    # --- Register agent handlers ---
    server = Server()
    for name, cfg in agents_cfg.items():
        mode = cfg.get("mode", "pty")
        if mode == "acp" and pool:
            agent_profile = cfg.get("profile")
            if agent_profile:
                # Inject litellm config into profile for harness-factory
                agent_profile.setdefault("litellm_url", litellm_cfg.get("url", ""))
                litellm_key = litellm_cfg.get("env", {}).get("LITELLM_API_KEY", "")
                agent_profile.setdefault("litellm_api_key", litellm_key)
            handler = make_acp_agent_handler(name, pool, profile=agent_profile)
        else:
            handler = make_pty_agent_handler(cfg, verbose=args.verbose)
        server.agent(name=name, description=cfg.get("description", ""),
                     metadata=Metadata(**cfg["metadata"]) if Metadata and cfg.get("metadata") else None)(handler)
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

    # --- S3 file sharing ---
    from src import s3 as s3_mod
    s3_cfg = config.get("s3", {})
    s3_ok = s3_mod.init(
        bucket=s3_cfg.get("bucket", ""),
        prefix=s3_cfg.get("prefix", "acp-bridge/files"),
        expires=s3_cfg.get("presign_expires", 3600),
    )
    if s3_ok:
        log.info("s3: file sharing enabled")

    # --- App + middleware ---
    app = create_app(*server.agents)

    # Extract the SDK's internal agents dict for dynamic registration
    for route in app.routes:
        if hasattr(route, 'name') and route.name == 'list_agents':
            for cell in route.endpoint.__closure__:
                try:
                    val = cell.cell_contents
                    if isinstance(val, dict) and all(
                        hasattr(v, 'name') for v in val.values()
                    ):
                        app.state.acp_agents = val
                        break
                except ValueError:
                    pass
            break

    app.add_middleware(SecurityMiddleware,
                       allowed_ips=sec_cfg.get("allowed_ips", []),
                       auth_token=sec_cfg.get("auth_token", ""),
                       rate_limit=sec_cfg.get("rate_limit", 60),
                       rate_window=sec_cfg.get("rate_window", 60),
                       max_body=sec_cfg.get("max_body_bytes", 3 * 1024 * 1024))

    # trace_id middleware — added last so it runs first (Starlette is LIFO)
    from src.trace import TraceIdMiddleware
    app.add_middleware(TraceIdMiddleware)

    # --- Job manager ---
    pty_agents = {k: v for k, v in agents_cfg.items() if v.get("mode") != "acp"}
    base_url = srv_cfg.get("base_url", f"http://{host}:{port}")
    job_mgr = JobManager(
        pool=pool, pty_configs=pty_agents,
        webhook_url=webhook_cfg.get("url", ""),
        webhook_token=webhook_cfg.get("token", ""),
        webhook_format=webhook_cfg.get("format", "openclaw"),
        webhook_secret=webhook_cfg.get("secret", ""),
        base_url=base_url,
    ) if (pool or pty_agents) else None

    # --- Fallback chain (load from YAML, fallback to built-in defaults) ---
    from src.agents import load_fallback_chain
    _config_dir = os.path.dirname(os.path.abspath(args.config)) if os.path.exists(args.config) else "."
    load_fallback_chain(os.path.join(_config_dir, "fallback-chain.yaml"))

    # --- Register routes ---
    start_time = time.time()
    webhook_account_id = webhook_cfg.get("account_id", "")
    webhook_default_target = webhook_cfg.get("target", webhook_cfg.get("discord_target", ""))
    openclaw_url = webhook_cfg.get("url", "")

    health_routes.register(app, _VERSION, start_time, agents_cfg, pool, ttl_hours,
                           job_mgr=job_mgr, litellm_cfg=litellm_cfg)
    jobs_routes.register(app, job_mgr, webhook_account_id, webhook_default_target)
    tools_routes.register(app, openclaw_url, webhook_cfg.get("token", ""), webhook_account_id)
    upload_dir = srv_cfg.get("upload_dir", "/tmp/acp-uploads")
    os.environ["ACP_UPLOAD_DIR"] = upload_dir
    files_routes.register(app, upload_dir)

    # --- Stats ---
    stats_collector = StatsCollector()
    stats_routes.register(app, stats_collector)
    import src.agents as _agents_mod
    _agents_mod._stats = stats_collector
    if job_mgr:
        job_mgr._stats = stats_collector

    # --- Heartbeat / env awareness ---
    from src.heartbeat import EnvCollector
    heartbeat_cfg = config.get("heartbeat", {})
    env_collector = None
    if heartbeat_cfg.get("enabled", False) and pool:
        env_collector = EnvCollector(pool, agents_cfg, port=port,
                                     client_script=heartbeat_cfg.get("client_script", ""),
                                     job_mgr=job_mgr,
                                     language=heartbeat_cfg.get("language", "en"),
                                     shared_workdir=srv_cfg.get("public_workdir", "/tmp/acp-public"))
        _agents_mod._env = env_collector
        from src.heartbeat import register as heartbeat_register
        heartbeat_register(app, env_collector, pool)
        log.info("heartbeat: env injection enabled for %s", sorted(env_collector._enabled_agents))

    # --- Templates ---
    templates_routes.register(app)

    # --- Dynamic harness ---
    harness_routes.register(app, pool, agents_cfg, litellm_cfg, harness_binary=harness_binary)

    # --- Pipeline manager ---
    from src.pipeline import PipelineManager
    conv_workdir = srv_cfg.get("public_workdir", srv_cfg.get("conversation_workdir", "/tmp/acp-pipelines"))
    agents_cfg["_public_workdir"] = conv_workdir
    pipeline_mgr = PipelineManager(pool, agents_cfg,
                                   webhook_url=webhook_cfg.get("url", ""),
                                   webhook_token=webhook_cfg.get("token", ""),
                                   webhook_format=webhook_cfg.get("format", "openclaw"),
                                   webhook_secret=webhook_cfg.get("secret", "")) if pool else None
    pipelines_routes.register(app, pipeline_mgr, webhook_account_id, webhook_default_target)

    if ui_enabled:
        chat_routes.register(app, config)

    # --- Lifespan ---
    from contextlib import asynccontextmanager

    busy_timeout = pool_cfg.get("busy_timeout", 360)

    async def cleanup_loop():
        while True:
            await asyncio.sleep(60)
            if pool:
                await pool.health_check(busy_timeout=busy_timeout)
                await pool.cleanup_idle(ttl_hours * 3600)
                await pool.memory_evict()
                pool.cleanup_ghosts()
            if job_mgr:
                job_mgr.cleanup()
            stats_collector.delete_old()

    heartbeat_interval = heartbeat_cfg.get("interval", 0)
    if env_collector:
        env_collector._interval = heartbeat_interval

    async def _heartbeat_ping_agent(agent_name: str):
        """Ping agent with LLM prompt for environment awareness."""
        import uuid
        from src.sse import transform_notification
        cfg = agents_cfg.get(agent_name, {})
        if not isinstance(cfg, dict) or cfg.get("mode") != "acp":
            return
        prompt = env_collector.build_heartbeat_prompt(agent_name)
        session_id = env_collector.heartbeat_session_id(agent_name)
        existing = pool._connections.get((agent_name, session_id))
        if existing and existing._busy:
            log.info("heartbeat_skip: agent=%s still busy", agent_name)
            return
        t0 = time.time()
        try:
            conn = await pool.get_or_create(agent_name, session_id,
                                            cwd=cfg.get("working_dir", "/tmp"))
            parts = []
            async for notification in conn.session_prompt(prompt):
                if "_prompt_result" in notification:
                    break
                event = transform_notification(notification)
                if event and event["type"] == "message.part":
                    parts.append(event["content"])
            response = "".join(parts).strip()
            silent = "[SILENT]" in response.upper() or not response
            env_collector.record(agent_name, prompt, response, silent, time.time() - t0,
                                 snapshot=env_collector.get_snapshot())
            env_collector.increment_round(agent_name)
            log.info("heartbeat_auto: agent=%s silent=%s dur=%.1fs round=%d",
                     agent_name, silent, time.time() - t0,
                     env_collector._round_counter.get(agent_name, 0))
        except Exception as e:
            log.warning("heartbeat_auto: agent=%s error=%s", agent_name, e)

    async def heartbeat_loop():
        """Independent heartbeat loop — dynamic interval, fire-and-forget per agent."""
        while True:
            interval = env_collector._interval if env_collector else heartbeat_interval
            if interval <= 0:
                await asyncio.sleep(10)
                continue
            await asyncio.sleep(interval)
            if not env_collector:
                continue
            env_collector.refresh()
            # Skip if nothing changed and no injected contexts
            if not env_collector.snapshot_changed() and not env_collector._injected_contexts:
                log.debug("heartbeat_skip: snapshot unchanged")
                continue
            for a in sorted(env_collector._enabled_agents):
                asyncio.create_task(_heartbeat_ping_agent(a))

    _original_lifespan = app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(application):
        async with _original_lifespan(application):
            if pool:
                pool.cleanup_ghosts()
            task = asyncio.create_task(cleanup_loop())
            if job_mgr:
                asyncio.create_task(job_mgr.run_recovery())
            if env_collector and heartbeat_interval > 0:
                asyncio.create_task(heartbeat_loop())
                log.info("heartbeat_loop: started, interval=%ds", heartbeat_interval)
            if pool:
                asyncio.create_task(ping_loop(pool))
                log.info("ping_loop: started, interval=300s")
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
        log.info("pool: max=%d max_per_agent=%d busy_timeout=%ds", pool_cfg.get("max_processes", 20), pool_cfg.get("max_per_agent", 10), busy_timeout)
    log.info("auth_token=%s", auth_token[:8] + "..." if len(auth_token) > 8 else auth_token)
    if job_mgr:
        log.info("jobs: monitor=60s stuck_timeout=600s webhook=%s", webhook_cfg.get("url", "(none)"))
    webhook_token = webhook_cfg.get("token", "")
    if webhook_cfg.get("url") and not webhook_token and webhook_cfg.get("format", "openclaw") != "generic":
        log.warning("webhook: url is set but token is empty — webhook calls will fail with 401. "
                    "Set OPENCLAW_TOKEN env var or check config.yaml")
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
        "║   🦞 OpenClaw ─┐              ┌──► 🤖 Kiro / Claude / Codex  ║\n"
        "║                 ┼──► acp 🌉 ──┼──► 🤖 Qwen / OpenCode       ║\n"
        "║   🌐 Web UI ──┘              └──► 🏭 Harness / ...          ║\n"
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
