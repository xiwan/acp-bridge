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
from src.prompt_log import PromptStore
from src.routes import admin as admin_routes
from src.routes import chat as chat_routes
from src.routes import files as files_routes
from src.routes import harness as harness_routes
from src.routes import health as health_routes
from src.routes import jobs as jobs_routes
from src.routes import pipelines as pipelines_routes
from src.routes import sessions as sessions_routes
from src.routes import stats as stats_routes
from src.routes import templates as templates_routes
from src.routes import tools as tools_routes
from src.security import SecurityMiddleware
from src.stats import StatsCollector

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
    # Botocore DEBUG records include fully rendered request headers, including
    # temporary AWS session credentials. Keep credential-bearing SDK internals
    # out of logs even when Bridge verbose logging is enabled.
    for logger_name in ("boto3", "botocore", "s3transfer"):
        logging.getLogger(logger_name).setLevel(logging.WARNING)
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

    if os.path.exists(args.config):
        config = load_config(args.config)
    else:
        from src.auto_detect import build_config

        config = build_config()
        agents = config.get("agents", {})
        if not agents:
            log.error("No config.yaml found and no agent CLIs detected in PATH")
            sys.exit(1)
        token = config["security"]["auth_token"]
        print(f"\n⚡ Zero-config mode: detected {len(agents)} agent(s): {', '.join(agents)}")
        print(f"🔑 Auth token: {token}")
        print("   (set ACP_BRIDGE_TOKEN env to use a fixed token)\n")

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
            resp = httpx.get(f"{litellm_url}/health/liveliness", timeout=5, headers=headers)
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
                print(
                    f"\n⚠️  LiteLLM ({litellm_url}) is not reachable — disabled agents: {', '.join(disabled)}\n"
                )
            if not agents_cfg:
                log.error("All agents disabled due to litellm dependency")
                sys.exit(1)

    # --- Process pool ---
    pool_cfg = config.get("pool", {})
    acp_agents = {k: v for k, v in agents_cfg.items() if v.get("mode") == "acp"}

    # Ensure all working dirs exist
    for cfg in agents_cfg.values():
        os.makedirs(cfg.get("working_dir", "/tmp"), exist_ok=True)
    pool = (
        AcpProcessPool(
            agents_config=acp_agents,
            max_processes=pool_cfg.get("max_processes", 20),
            max_per_agent=pool_cfg.get("max_per_agent", 10),
            verbose=args.verbose,
        )
        if acp_agents
        else None
    )
    if pool:
        pool._memory_limit_pct = pool_cfg.get("memory_limit_percent", 80)
        pool._acquire_timeout = pool_cfg.get("acquire_timeout", 60)

    # --- Register agent handlers ---
    # Lambda pool reference — will be populated later if enabled.
    # Agents with pool="lambda" are registered after lambda_pool init.
    _lambda_agent_defs: list[tuple[str, dict]] = []

    server = Server()
    for name, cfg in agents_cfg.items():
        mode = cfg.get("mode", "pty")
        agent_pool_type = cfg.get("pool", "local")  # "local" (default) or "lambda"

        if agent_pool_type == "lambda":
            # Defer registration until lambda_pool is initialized
            _lambda_agent_defs.append((name, cfg))
            continue

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
        # Build metadata, marking this agent as locally served (symmetric with mesh
        # remote agents, which get "mesh"/"node:<name>" tags in mesh_client.reconcile()).
        # Metadata must be set at construction (Agent.metadata is read-only afterwards).
        agent_metadata = None
        if Metadata:
            md = dict(cfg.get("metadata") or {})
            md["tags"] = ["local"] + list(md.get("tags") or [])
            agent_metadata = Metadata(**md)
        server.agent(name=name, description=cfg.get("description", ""), metadata=agent_metadata)(
            handler
        )
        log.info("registered: agent=%s mode=%s cmd=%s", name, mode, cfg.get("command"))

    # --- Config values ---
    sec_cfg = config.get("security", {})
    srv_cfg = config.get("server", {})
    webhook_cfg = config.get("webhook", {})

    auth_token = sec_cfg.get("auth_token", "")
    if not auth_token:
        log.error("security.auth_token resolved to an empty value; refusing to start")
        log.error("Set ACP_BRIDGE_TOKEN or configure security.auth_token explicitly")
        sys.exit(1)

    # SSRF guard for client-supplied outbound URLs (jobs.callback_url, mesh
    # ws_in/ws_out). Empty by default (blocks all loopback/link-local/
    # private/reserved targets); list specific trusted hosts/CIDRs here for
    # a private-network callback target (e.g. a self-hosted n8n instance).
    # Cloud metadata hosts/IPs are always blocked, regardless of this list.
    allowed_private_targets = frozenset(sec_cfg.get("allowed_private_targets", []))

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
    # SDK hotfixes (see src/acp_patch.py): per-key watch Events instead of the
    # SDK's global one (thundering herd — every store write woke every run
    # watcher), and cancellation-watcher reaping (zombie watchers accumulated
    # per finished run). Root cause of the 2026-08-11 100%-CPU incident.
    from datetime import timedelta

    from src.acp_patch import PerKeyEventMemoryStore, apply_executor_patch

    apply_executor_patch()
    app = create_app(
        *server.agents, store=PerKeyEventMemoryStore(limit=1000, ttl=timedelta(hours=1))
    )

    # Extract the SDK's internal agents dict for dynamic registration
    for route in app.routes:
        if hasattr(route, "name") and route.name == "list_agents":
            for cell in route.endpoint.__closure__:
                try:
                    val = cell.cell_contents
                    if isinstance(val, dict) and all(hasattr(v, "name") for v in val.values()):
                        app.state.acp_agents = val
                        break
                except ValueError:
                    pass
            break

    app.add_middleware(
        SecurityMiddleware,
        allowed_ips=sec_cfg.get("allowed_ips", []),
        auth_token=sec_cfg.get("auth_token", ""),
        rate_limit=sec_cfg.get("rate_limit", 60),
        rate_window=sec_cfg.get("rate_window", 60),
        max_body=sec_cfg.get("max_body_bytes", 3 * 1024 * 1024),
    )

    # trace_id middleware — added last so it runs first (Starlette is LIFO)
    from src.trace import TraceIdMiddleware

    app.add_middleware(TraceIdMiddleware)

    # --- Job manager ---
    pty_agents = {k: v for k, v in agents_cfg.items() if v.get("mode") != "acp"}
    base_url = srv_cfg.get("base_url", f"http://{host}:{port}")
    # Prompt log (records every prompt actually sent to an agent).
    pl_cfg = config.get("prompt_log", {})
    prompt_store = None
    if pl_cfg.get("enabled", True):
        prompt_store = PromptStore(
            db_path=pl_cfg.get("db_path", "data/jobs.db"),
            redact=pl_cfg.get("redact_secrets", True),
            max_size=int(pl_cfg.get("max_size", 1_048_576)),
        )
        log.info(
            "prompt_log enabled (redact=%s, max_size=%d)",
            pl_cfg.get("redact_secrets", True),
            int(pl_cfg.get("max_size", 1_048_576)),
        )
    job_mgr = (
        JobManager(
            pool=pool,
            pty_configs=pty_agents,
            webhook_url=webhook_cfg.get("url", ""),
            webhook_token=webhook_cfg.get("token", ""),
            webhook_format=webhook_cfg.get("format", "openclaw"),
            webhook_secret=webhook_cfg.get("secret", ""),
            base_url=base_url,
            prompt_store=prompt_store,
            allowed_private_targets=allowed_private_targets,
        )
        if (pool or pty_agents)
        else None
    )

    # --- Fallback chain (load from YAML, fallback to built-in defaults) ---
    from src.agents import load_fallback_chain

    _config_dir = (
        os.path.dirname(os.path.abspath(args.config)) if os.path.exists(args.config) else "."
    )
    load_fallback_chain(os.path.join(_config_dir, "fallback-chain.yaml"))

    # --- Register routes ---
    start_time = time.time()
    webhook_account_id = webhook_cfg.get("account_id", "")
    webhook_default_target = webhook_cfg.get("target", webhook_cfg.get("discord_target", ""))
    openclaw_url = webhook_cfg.get("url", "")

    health_routes.register(
        app,
        _VERSION,
        start_time,
        agents_cfg,
        pool,
        ttl_hours,
        job_mgr=job_mgr,
        litellm_cfg=litellm_cfg,
    )
    sessions_routes.register(app, pool, agents_cfg)
    jobs_routes.register(
        app, job_mgr, webhook_account_id, webhook_default_target, prompt_store=prompt_store
    )
    tools_routes.register(app, openclaw_url, webhook_cfg.get("token", ""), webhook_account_id)
    upload_dir = srv_cfg.get("upload_dir", "/tmp/acp-uploads")
    os.environ["ACP_UPLOAD_DIR"] = upload_dir
    files_routes.register(app, upload_dir)

    # --- LiteLLM proxy + usage tracking ---
    from src.routes import litellm_proxy as litellm_routes

    litellm_routes.register(app, litellm_cfg)

    # --- Stats ---
    stats_collector = StatsCollector()
    stats_routes.register(app, stats_collector)
    import src.agents as _agents_mod

    _agents_mod._stats = stats_collector
    if job_mgr:
        job_mgr._stats = stats_collector
        job_mgr._app = app

    # --- Heartbeat / env awareness ---
    from src.heartbeat import EnvCollector

    heartbeat_cfg = config.get("heartbeat", {})
    env_collector = None
    if heartbeat_cfg.get("enabled", False) and pool:
        active_hours_cfg = heartbeat_cfg.get("active_hours", [0, 24])
        env_collector = EnvCollector(
            pool,
            agents_cfg,
            port=port,
            client_script=heartbeat_cfg.get("client_script", ""),
            job_mgr=job_mgr,
            language=heartbeat_cfg.get("language", "en"),
            shared_workdir=srv_cfg.get("public_workdir", "/tmp/acp-public"),
            active_hours=tuple(active_hours_cfg),
            timezone_offset=heartbeat_cfg.get("timezone_offset", 8),
        )
        _agents_mod._env = env_collector
        from src.heartbeat import register as heartbeat_register

        heartbeat_register(app, env_collector, pool, prompt_store=prompt_store)
        log.info(
            "heartbeat: env injection enabled for %s (active %d:00-%d:00 UTC+%d)",
            sorted(env_collector._enabled_agents),
            active_hours_cfg[0],
            active_hours_cfg[1],
            heartbeat_cfg.get("timezone_offset", 8),
        )

    # --- Templates ---
    templates_routes.register(app)

    # --- Dynamic harness ---
    harness_routes.register(app, pool, agents_cfg, litellm_cfg, harness_binary=harness_binary)

    # --- Lambda Pool (serverless burst) ---
    from src.agents import make_lambda_agent_handler
    from src.routes import lambda_pool as lambda_pool_routes

    lambda_pool_cfg = config.get("lambda_pool", {})
    lambda_pool_instance = None
    if lambda_pool_cfg.get("enabled", False):
        from src.lambda_pool import LambdaPool

        lambda_pool_instance = LambdaPool(
            function_name=lambda_pool_cfg["function_name"],
            region=lambda_pool_cfg.get("region", "us-east-1"),
            max_concurrent=lambda_pool_cfg.get("max_concurrent", 100),
            timeout=lambda_pool_cfg.get("timeout", 300),
            default_model=lambda_pool_cfg.get(
                "default_model", "bedrock/anthropic.claude-sonnet-4-6"
            ),
        )
        log.info(
            "lambda_pool: enabled fn=%s max=%d",
            lambda_pool_cfg["function_name"],
            lambda_pool_cfg.get("max_concurrent", 100),
        )

    # Register deferred lambda agents (pool="lambda" in config).
    # NOTE: create_app() above already snapshotted server.agents, so calling
    # server.agent() here would register into a list nobody reads — the agent
    # would be invisible to /runs. Insert the manifest into the live dict the
    # SDK actually serves, the same way src/routes/harness.py registers
    # dynamically-created harness agents.
    _live_agents = getattr(app.state, "acp_agents", None)
    for name, cfg in _lambda_agent_defs:
        if not lambda_pool_instance:
            log.warning("agent %s has pool=lambda but lambda_pool is not enabled, skipping", name)
            continue
        agent_profile = cfg.get("profile")
        agent_model = cfg.get("model", lambda_pool_cfg.get("default_model", ""))
        handler = make_lambda_agent_handler(
            name, lambda_pool_instance, profile=agent_profile, model=agent_model
        )
        agent_metadata = None
        if Metadata:
            md = dict(cfg.get("metadata") or {})
            md["tags"] = ["lambda"] + list(md.get("tags") or [])
            agent_metadata = Metadata(**md)
        _srv = Server()
        _srv.agent(name=name, description=cfg.get("description", ""), metadata=agent_metadata)(
            handler
        )
        if _live_agents is None:
            log.error("cannot register lambda agent %s: SDK agents dict unavailable", name)
            continue
        _live_agents[_srv.agents[0].name] = _srv.agents[0]
        log.info(
            "registered: agent=%s mode=lambda pool=%s", name, lambda_pool_cfg.get("function_name")
        )

    lambda_pool_routes.register(app, lambda_pool_instance)

    # --- Pipeline manager ---
    from src.pipeline import PipelineManager

    conv_workdir = srv_cfg.get(
        "public_workdir", srv_cfg.get("conversation_workdir", "/tmp/acp-pipelines")
    )
    agents_cfg["_public_workdir"] = conv_workdir
    pipeline_mgr = (
        PipelineManager(
            pool,
            agents_cfg,
            webhook_url=webhook_cfg.get("url", ""),
            webhook_token=webhook_cfg.get("token", ""),
            webhook_format=webhook_cfg.get("format", "openclaw"),
            webhook_secret=webhook_cfg.get("secret", ""),
            allowed_private_targets=allowed_private_targets,
            prompt_store=prompt_store,
        )
        if pool
        else None
    )
    if pipeline_mgr and lambda_pool_instance:
        pipeline_mgr._lambda_pool = lambda_pool_instance
    pipelines_routes.register(
        app, pipeline_mgr, webhook_account_id, webhook_default_target, prompt_store=prompt_store
    )
    admin_routes.register(app, prompt_store=prompt_store)

    # --- A2A Mesh L0 (optional, decentralized discovery) ---
    mesh_cfg = config.get("mesh", {})
    mesh_mgr = None
    if mesh_cfg.get("enabled", False):
        from src.mesh import MeshManager, resolve_mesh_token
        from src.routes import mesh as mesh_routes

        try:
            mesh_token = resolve_mesh_token(mesh_cfg)
        except ValueError as e:
            log.error(str(e))
            log.error("Set mesh.token (e.g. via ${MESH_TOKEN}) or disable mesh.enabled")
            sys.exit(1)
        mesh_mgr = MeshManager(
            **{
                "node_name": mesh_cfg.get("node_id", f"{host}:{port}"),
                "self_url": mesh_cfg.get("self_url", base_url),
                "version": _VERSION,
                "agents_cfg": {k: v for k, v in agents_cfg.items() if isinstance(v, dict)},
                "config_path": args.config,
                "seeds": mesh_cfg.get("seeds", []),
                "token": mesh_token,
                "announce_interval": mesh_cfg.get("announce_interval", 300),
                "max_hops": mesh_cfg.get("max_hops", 1),
                "pricing": mesh_cfg.get("pricing"),
                "mode": mesh_cfg.get("mode", ""),
                "private_url": mesh_cfg.get("private_url", ""),
                "public_url": mesh_cfg.get("public_url", ""),
            }
        )
        # L1: A2A Server — reuse existing agent handlers (no new exec logic).
        from src.mesh_a2a import A2AAdapter

        _remote_skills: set = set()
        a2a_adapter = A2AAdapter(
            agents_provider=lambda: getattr(app.state, "acp_agents", {}),
            job_mgr=job_mgr,
            remote_skills=_remote_skills,
            pool=pool,  # L3: run a workspace step with an explicit cwd
            allowed_private_targets=allowed_private_targets,
        )
        mesh_routes.register(app, mesh_mgr, adapter=a2a_adapter)
        # L2: A2A Client — register remote handlers for peer-only skills each cycle.
        from src.mesh_client import reconcile as _mesh_reconcile

        mesh_mgr.on_cycle = lambda: _mesh_reconcile(app, mesh_mgr, _remote_skills)

        # L3: let pipeline steps relay shared_cwd to the peer owning a remote skill.
        def _mesh_resolver(agent_name):
            if agent_name not in _remote_skills:
                return None
            for p in mesh_mgr._peers.values():
                if p.healthy and agent_name in p.skills:
                    return (p.url, mesh_mgr.token)
            return None

        if pipeline_mgr:
            pipeline_mgr._mesh_resolver = _mesh_resolver
        log.info(
            "mesh: enabled node=%s seeds=%s agents=%s (L1 a2a on, L2 routing on, L3 workspace relay on)",
            mesh_mgr.node_name,
            mesh_mgr.seeds,
            mesh_mgr._agent_names(),
        )
        # Wire mesh agents into heartbeat snapshot
        if env_collector:
            env_collector._acp_agents_provider = lambda: getattr(app.state, "acp_agents", {})

    chat_store = None
    if ui_enabled:
        chat_store = chat_routes.register(app, config)

    # --- Lifespan ---
    from contextlib import asynccontextmanager

    busy_timeout = pool_cfg.get("busy_timeout", 360)
    ws_ttl_hours = srv_cfg.get("workspace_ttl_hours", 72)

    # Data retention (v0.41.0) — these tables previously grew unbounded.
    # prompt_log honors its pre-existing (but never wired) retention_days key.
    retention_cfg = config.get("retention", {})
    pipeline_retention = retention_cfg.get("pipelines_days", 7) * 86400
    chat_retention = retention_cfg.get("chat_days", 7) * 86400
    prompt_retention = pl_cfg.get("retention_days", 30) * 86400
    usage_retention = retention_cfg.get("llm_usage_days", 30) * 86400

    async def cleanup_loop():
        from src import workspace

        while True:
            await asyncio.sleep(60)
            if pool:
                await pool.health_check(busy_timeout=busy_timeout)
                await pool.cleanup_idle(ttl_hours * 3600)
                await pool.memory_evict()
                pool.flush_pids()
                pool.cleanup_ghosts()
            if job_mgr:
                job_mgr.cleanup()
            if pipeline_mgr:
                pipeline_mgr.cleanup()
                await asyncio.to_thread(pipeline_mgr._store.delete_old, pipeline_retention)
                if ws_ttl_hours > 0:
                    await asyncio.to_thread(
                        workspace.sweep,
                        conv_workdir,
                        ws_ttl_hours * 3600,
                        pipeline_mgr.active_cwds(),
                    )
            await asyncio.to_thread(stats_collector.delete_old)
            if prompt_store:
                await asyncio.to_thread(prompt_store.cleanup_older_than, prompt_retention)
            if chat_store:
                await asyncio.to_thread(chat_store.delete_old, chat_retention)
            await asyncio.to_thread(litellm_routes.delete_old, usage_retention)

    heartbeat_interval = heartbeat_cfg.get("interval", 0)
    if env_collector:
        env_collector._interval = heartbeat_interval

    async def _heartbeat_ping_agent(agent_name: str):
        """Ping agent with LLM prompt for environment awareness."""
        from src.heartbeat import HEARTBEAT_IDLE_TIMEOUT
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
            conn = await pool.get_or_create(
                agent_name, session_id, cwd=cfg.get("working_dir", "/tmp")
            )
            parts = []
            async for notification in conn.session_prompt(
                prompt, idle_timeout=HEARTBEAT_IDLE_TIMEOUT
            ):
                if "_prompt_result" in notification:
                    from src.agents import _record_acp_usage

                    await asyncio.to_thread(
                        _record_acp_usage,
                        agent_name,
                        notification["_prompt_result"],
                        time.time() - t0,
                    )
                    break
                event = transform_notification(notification)
                if event and event["type"] == "message.part":
                    parts.append(event["content"])
            response = env_collector.clean_response("".join(parts).strip())
            silent = env_collector.is_silent(response)
            env_collector.record(
                agent_name,
                prompt,
                response,
                silent,
                time.time() - t0,
                snapshot=env_collector.get_snapshot(),
            )
            env_collector.increment_round(agent_name)
            log.info(
                "heartbeat_auto: agent=%s silent=%s dur=%.1fs round=%d",
                agent_name,
                silent,
                time.time() - t0,
                env_collector._round_counter.get(agent_name, 0),
            )
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
            # Time-window gate
            if not env_collector.is_active_time():
                log.debug("heartbeat_skip: outside active hours")
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
            if pipeline_mgr:
                asyncio.create_task(pipeline_mgr.run_recovery())
            if env_collector and heartbeat_interval > 0:
                asyncio.create_task(heartbeat_loop())
                log.info("heartbeat_loop: started, interval=%ds", heartbeat_interval)
            if pool:
                asyncio.create_task(ping_loop(pool))
                log.info("ping_loop: started, interval=300s")
            if mesh_mgr:
                asyncio.create_task(mesh_mgr.announce_loop())
                log.info("mesh: announce_loop started, interval=%ds", mesh_mgr.announce_interval)
            yield
            task.cancel()
            if pool:
                log.info("shutting down, killing all subprocesses...")
                await pool.shutdown()

    app.router.lifespan_context = lifespan

    # --- Logging ---
    log.info("allowed_ips=%s", sec_cfg.get("allowed_ips", []))
    if pool:
        log.info(
            "pool: max=%d max_per_agent=%d busy_timeout=%ds",
            pool_cfg.get("max_processes", 20),
            pool_cfg.get("max_per_agent", 10),
            busy_timeout,
        )
    log.info("authentication configured")
    if job_mgr:
        log.info(
            "jobs: monitor=60s stuck_timeout=600s webhook=%s", webhook_cfg.get("url", "(none)")
        )
    webhook_token = webhook_cfg.get("token", "")
    if (
        webhook_cfg.get("url")
        and not webhook_token
        and webhook_cfg.get("format", "openclaw") != "generic"
    ):
        log.warning(
            "webhook: url is set but token is empty — webhook calls will fail with 401. "
            "Set OPENCLAW_TOKEN env var or check config.yaml"
        )
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
    import atexit
    import signal as _sig

    def _kill_all():
        if pool:
            for (a, s), conn in list(pool._connections.items()):
                try:
                    os.killpg(conn.proc.pid, _sig.SIGKILL)
                except (ProcessLookupError, PermissionError, OSError):
                    pass

    atexit.register(_kill_all)

    uvicorn.run(
        app,
        host=host,
        port=port,
        log_level="debug" if args.verbose else "info",
        timeout_graceful_shutdown=shutdown_timeout,
        loop="asyncio",
    )


if __name__ == "__main__":
    main()
