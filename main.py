import argparse
import asyncio
import logging
import shutil
import sys
import time
from pathlib import Path

import uvicorn
import yaml
from acp_sdk.server import Server
from acp_sdk.server.app import create_app

from src.agents import make_agent_handler
from src.security import SecurityMiddleware

log = logging.getLogger("acp-bridge")


def setup_logging(verbose: bool):
    level = logging.DEBUG if verbose else logging.INFO
    fmt = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    logging.basicConfig(level=level, format=fmt)
    if not verbose:
        logging.getLogger("uvicorn.access").setLevel(logging.WARNING)


def load_config(path: str = "config.yaml") -> dict:
    with open(path) as f:
        return yaml.safe_load(f)


def main():
    parser = argparse.ArgumentParser(description="ACP Bridge Server")
    parser.add_argument("--host", help="Override listen host")
    parser.add_argument("--port", type=int, help="Override listen port")
    parser.add_argument("--config", default="config.yaml", help="Config file path")
    parser.add_argument("--verbose", "-v", action="store_true", help="Enable verbose/debug logging")
    args = parser.parse_args()

    setup_logging(args.verbose)

    config = load_config(args.config)
    log.debug("Loaded config from %s", args.config)

    agents = {k: v for k, v in config.get("agents", {}).items() if v.get("enabled")}
    if not agents:
        log.error("No enabled agents in config. Check config.yaml")
        sys.exit(1)

    server = Server()
    for name, cfg in agents.items():
        handler = make_agent_handler(cfg, verbose=args.verbose)
        server.agent(name=name, description=cfg.get("description", ""))(handler)
        log.info("✅ Registered agent: %s  cmd=%s output_mode=%s", name, cfg["command"], cfg.get("output_mode", "raw"))

    allowed_ips = config.get("security", {}).get("allowed_ips", [])
    auth_token = config.get("security", {}).get("auth_token", "")
    srv_cfg = config.get("server", {})
    host = args.host or srv_cfg.get("host", "0.0.0.0")
    port = args.port or srv_cfg.get("port", 8001)

    app = create_app(*server.agents)
    app.add_middleware(SecurityMiddleware, allowed_ips=allowed_ips, auth_token=auth_token)

    # Session cleanup task
    ttl_hours = srv_cfg.get("session_ttl_hours", 24)
    session_bases = {cfg.get("session_base", "/tmp/acp-bridge-sessions") for cfg in agents.values()}

    async def cleanup_sessions():
        while True:
            await asyncio.sleep(3600)  # check every hour
            cutoff = time.time() - ttl_hours * 3600
            for base in session_bases:
                base_path = Path(base)
                if not base_path.exists():
                    continue
                for d in base_path.iterdir():
                    if d.is_dir() and d.stat().st_mtime < cutoff:
                        shutil.rmtree(d, ignore_errors=True)
                        log.info("🧹 Cleaned expired session: %s", d.name)

    @app.on_event("startup")
    async def start_cleanup():
        asyncio.create_task(cleanup_sessions())

    log.info("🔒 Allowed IPs: %s", allowed_ips)
    log.info("🧹 Session TTL: %d hours", ttl_hours)
    log.info("🚀 Starting ACP server on %s:%s", host, port)

    uvicorn.run(app, host=host, port=port, log_level="debug" if args.verbose else "info")


if __name__ == "__main__":
    main()
