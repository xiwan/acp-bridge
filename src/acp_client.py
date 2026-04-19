"""ACP stdio JSON-RPC client — manages CLI agent subprocesses."""

import asyncio
import json
import logging
import os
import signal
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, AsyncIterator

log = logging.getLogger("acp-bridge.acp_client")

_VERSION = (Path(__file__).resolve().parent.parent / "VERSION").read_text().strip()


class AcpError(Exception):
    pass


class PoolExhaustedError(AcpError):
    pass


@dataclass
class AcpConnection:
    agent: str
    session_id: str
    proc: asyncio.subprocess.Process
    verbose: bool = False
    _req_id: int = field(default=0, init=False)
    _pending: dict[int, asyncio.Future] = field(default_factory=dict, init=False)
    _reader_task: asyncio.Task | None = field(default=None, init=False)
    _stderr_task: asyncio.Task | None = field(default=None, init=False)
    _notification_queues: dict[int, asyncio.Queue] = field(default_factory=dict, init=False)
    acp_session_id: str | None = field(default=None, init=False)
    resolved_model: str | None = field(default=None, init=False)
    last_active: float = field(default_factory=time.time, init=False)
    session_reset: bool = field(default=False, init=False)
    _busy: bool = field(default=False, init=False)

    def _next_id(self) -> int:
        self._req_id += 1
        return self._req_id

    async def _send(self, msg: dict) -> None:
        data = json.dumps(msg) + "\n"
        if self.verbose:
            log.debug("acp_send: %s", data.rstrip()[:500])
        self.proc.stdin.write(data.encode())
        await self.proc.stdin.drain()

    async def _send_request(self, method: str, params: dict | None = None) -> Any:
        req_id = self._next_id()
        msg: dict[str, Any] = {"jsonrpc": "2.0", "id": req_id, "method": method}
        if params is not None:
            msg["params"] = params
        fut: asyncio.Future[dict] = asyncio.get_event_loop().create_future()
        self._pending[req_id] = fut
        await self._send(msg)
        result = await fut
        if "error" in result:
            raise AcpError(f"ACP error on {method}: {result['error']}")
        return result.get("result")

    async def _send_notification(self, method: str, params: dict | None = None) -> None:
        msg: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            msg["params"] = params
        await self._send(msg)

    def _start_reader(self) -> None:
        self._reader_task = asyncio.create_task(self._read_loop())
        self._stderr_task = asyncio.create_task(self._drain_stderr())

    async def _drain_stderr(self) -> None:
        try:
            while True:
                try:
                    line = await self.proc.stderr.readline()
                except (asyncio.LimitOverrunError, ValueError):
                    # line exceeded StreamReader limit — skip to next newline
                    continue
                if not line:
                    break
                if self.verbose:
                    log.debug("acp_stderr: %s", line.decode().rstrip()[:300])
        except Exception:
            pass

    async def _read_loop(self) -> None:
        try:
            while True:
                try:
                    line = await self.proc.stdout.readline()
                except (asyncio.LimitOverrunError, ValueError):
                    log.warning("acp_read: line too long, skipping")
                    continue
                if not line:
                    break
                try:
                    msg = json.loads(line.decode())
                except json.JSONDecodeError:
                    continue
                if self.verbose:
                    log.debug("acp_recv: %s", line.decode().rstrip()[:500])
                msg_id = msg.get("id")
                if msg_id is not None and msg_id in self._pending:
                    self._pending.pop(msg_id).set_result(msg)
                else:
                    # Auto-reply permission requests (e.g. claude-agent-acp)
                    if msg.get("method") == "session/request_permission" and msg_id is not None:
                        log.info("auto-allow permission: %s",
                                 msg.get("params", {}).get("toolCall", {}).get("title", "?"))
                        reply = {"jsonrpc": "2.0", "id": msg_id,
                                 "result": {"outcome": {"outcome": "selected",
                                                        "optionId": "allow_always"}}}
                        data = json.dumps(reply) + "\n"
                        self.proc.stdin.write(data.encode())
                        asyncio.ensure_future(self.proc.stdin.drain())
                    for q in self._notification_queues.values():
                        q.put_nowait(msg)
        except Exception as e:
            log.error("reader loop crashed: %s", e)
        finally:
            for fut in self._pending.values():
                if not fut.done():
                    fut.set_result({"error": {"code": -1, "message": "connection closed"}})
            self._pending.clear()
            for q in self._notification_queues.values():
                q.put_nowait(None)

    def _subscribe(self) -> tuple[int, asyncio.Queue]:
        sub_id = id(asyncio.current_task())
        q: asyncio.Queue = asyncio.Queue()
        self._notification_queues[sub_id] = q
        return sub_id, q

    def _unsubscribe(self, sub_id: int) -> None:
        self._notification_queues.pop(sub_id, None)

    @property
    def alive(self) -> bool:
        return self.proc.returncode is None

    @property
    def state(self) -> str:
        """Read-only view of connection state: dead | busy | stale | idle."""
        if not self.alive:
            return "dead"
        if self._busy:
            return "busy"
        if self.session_reset:
            return "stale"
        return "idle"

    async def initialize(self) -> dict:
        self._start_reader()
        result = await self._send_request("initialize", {
            "protocolVersion": 1,
            "clientCapabilities": {},
            "clientInfo": {"name": "acp-bridge", "version": _VERSION},
        })
        log.info("initialized: agent=%s version=%s",
                 result.get("agentInfo", {}).get("name"),
                 result.get("agentInfo", {}).get("version"))
        return result

    async def session_new(self, cwd: str, profile: dict | None = None) -> str:
        sub_id, q = self._subscribe()
        try:
            params = {"cwd": cwd, "mcpServers": []}
            if profile:
                params["profile"] = profile
            result = await self._send_request("session/new", params)
            self.acp_session_id = result["sessionId"]
            # harness-factory 0.8.0+: resolvedModel inside result.activated
            activated = result.get("activated") if isinstance(result, dict) else None
            if isinstance(activated, dict) and activated.get("resolvedModel"):
                self.resolved_model = activated["resolvedModel"]
            log.info("session created: acp_session=%s%s",
                     self.acp_session_id,
                     f" model={self.resolved_model}" if getattr(self, "resolved_model", None) else "")
            return self.acp_session_id
        finally:
            self._unsubscribe(sub_id)

    async def session_prompt(self, prompt: str, idle_timeout: float = 300) -> AsyncIterator[dict]:
        self._busy = True
        self.last_active = time.time()
        last_event_time = time.time()
        sub_id, q = self._subscribe()
        req_id = self._next_id()
        msg = {
            "jsonrpc": "2.0", "id": req_id, "method": "session/prompt",
            "params": {
                "sessionId": self.acp_session_id,
                "prompt": [{"type": "text", "text": prompt}],
            },
        }
        fut: asyncio.Future[dict] = asyncio.get_event_loop().create_future()
        self._pending[req_id] = fut
        await self._send(msg)

        try:
            while True:
                if fut.done():
                    break
                if time.time() - last_event_time > idle_timeout:
                    yield {"_prompt_result": {"error": {"code": -1, "message": "agent_timeout (idle)"}}}
                    return
                try:
                    notification = await asyncio.wait_for(q.get(), timeout=1.0)
                    if notification is None:
                        break
                    last_event_time = time.time()
                    yield notification
                except asyncio.TimeoutError:
                    continue

            while not q.empty():
                n = q.get_nowait()
                if n is not None:
                    yield n

            # Give reader task a moment to flush remaining notifications
            await asyncio.sleep(0.05)
            while not q.empty():
                n = q.get_nowait()
                if n is not None:
                    yield n

            result = fut.result() if fut.done() else {"error": {"code": -1, "message": "no response"}}
            yield {"_prompt_result": result}
        finally:
            self._unsubscribe(sub_id)
            self.last_active = time.time()
            self._busy = False

    async def session_cancel(self) -> None:
        await self._send_notification("session/cancel", {
            "sessionId": self.acp_session_id,
        })

    async def kill(self) -> None:
        if self.alive:
            try:
                os.killpg(self.proc.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                self.proc.kill()
            await self.proc.wait()
        for task in (self._reader_task, self._stderr_task):
            if task and not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

    async def ping(self, timeout: float = 5) -> bool:
        """Lightweight health probe — send a no-op JSON-RPC request."""
        if not self.alive:
            return False
        try:
            await asyncio.wait_for(
                self._send_request("ping", {}), timeout=timeout
            )
            return True
        except AcpError:
            # Agent replied with an error (method not found) — still alive
            return True
        except Exception:
            return False


class AcpProcessPool:
    def __init__(self, agents_config: dict, max_processes: int = 20, max_per_agent: int = 10, verbose: bool = False):
        self._config = agents_config
        self._max = max_processes
        self._max_per_agent = max_per_agent
        self._verbose = verbose
        self._connections: dict[tuple[str, str], AcpConnection] = {}
        self._memory_limit_pct: float = 80.0

    @staticmethod
    def _agent_group(agent: str) -> str:
        """Map agent name to its group for shared limits.
        All harness-* agents share the 'harness' group."""
        if agent.startswith("harness") :
            return "harness"
        return agent

    def _count_agent(self, agent: str) -> int:
        group = self._agent_group(agent)
        return sum(1 for (a, _) in self._connections if self._agent_group(a) == group)

    def _lru_idle(self, agent: str | None = None) -> tuple[str, str] | None:
        """Return key of least-recently-used idle connection, optionally filtered by agent group."""
        if agent is not None:
            group = self._agent_group(agent)
            candidates = [
                (k, c) for k, c in self._connections.items()
                if not c._busy and c.alive and self._agent_group(k[0]) == group
            ]
        else:
            candidates = [
                (k, c) for k, c in self._connections.items()
                if not c._busy and c.alive
            ]
        if not candidates:
            return None
        return min(candidates, key=lambda x: x[1].last_active)[0]

    def _lru_idle_exact(self, agent: str) -> tuple[str, str] | None:
        """Return LRU idle connection for the exact agent name (no group matching)."""
        candidates = [
            (k, c) for k, c in self._connections.items()
            if not c._busy and c.alive and k[0] == agent
        ]
        if not candidates:
            return None
        return min(candidates, key=lambda x: x[1].last_active)[0]

    async def _evict(self, key: tuple[str, str]) -> None:
        """Kill and remove a connection from the pool."""
        conn = self._connections.pop(key, None)
        if conn:
            log.info("lru_evict: agent=%s session=%s idle=%.0fs",
                     key[0], key[1], time.time() - conn.last_active)
            await conn.kill()
            self._save_pids()

    async def _reuse(self, old_key: tuple[str, str], new_key: tuple[str, str], cwd: str, profile: dict | None = None) -> AcpConnection:
        """Reuse an existing connection under a new session key — reset context via session/new."""
        conn = self._connections.pop(old_key)
        new_agent, new_session_id = new_key
        log.info("lru_reuse: agent=%s session=%s→%s", new_agent, old_key[1], new_session_id)
        conn.session_id = new_session_id
        conn.session_reset = True
        await conn.session_new(cwd or self._config[new_agent].get("working_dir", "/tmp"), profile=profile)
        self._connections[new_key] = conn
        self._save_pids()
        return conn

    async def get_or_create(self, agent: str, session_id: str, cwd: str = "", profile: dict | None = None) -> AcpConnection:
        key = (agent, session_id)
        conn = self._connections.get(key)

        if conn and conn.alive:
            return conn

        # Resolve profile: explicit param > agent config
        if profile is None:
            profile = self._config.get(agent, {}).get("profile")

        is_rebuild = conn is not None
        if conn:
            log.warning("stale connection: agent=%s session=%s, rebuilding", agent, session_id)
            self._connections.pop(key, None)

        # per-agent limit: evict LRU idle same-agent connection to free a slot
        if self._count_agent(agent) >= self._max_per_agent:
            lru = self._lru_idle(agent=agent)
            if lru is None:
                raise PoolExhaustedError(f"per-agent limit for {agent} ({self._max_per_agent}), all busy")
            await self._evict(lru)

        # global limit: prefer reusing same-agent process, else evict globally LRU
        if len(self._connections) >= self._max:
            lru_same = self._lru_idle_exact(agent)
            if lru_same:
                return await self._reuse(lru_same, key, cwd, profile=profile)
            lru = self._lru_idle()
            if lru is None:
                raise PoolExhaustedError(f"global limit ({self._max}), all connections busy")
            await self._evict(lru)

        agent_cfg = self._config.get(agent)
        if not agent_cfg:
            raise AcpError(f"agent not found: {agent}")

        conn = await self._spawn(agent, session_id, agent_cfg, is_rebuild=is_rebuild, cwd_override=cwd, profile=profile)
        self._connections[key] = conn
        self._save_pids()
        return conn

    async def _spawn(self, agent: str, session_id: str, cfg: dict, is_rebuild: bool = False, cwd_override: str = "", profile: dict | None = None) -> AcpConnection:
        command = cfg["command"]
        acp_args = cfg.get("acp_args", ["acp"])
        cwd = cwd_override or cfg.get("working_dir", "/tmp")
        os.makedirs(cwd, exist_ok=True)

        log.info("spawning: agent=%s session=%s cmd=%s %s rebuild=%s", agent, session_id, command, acp_args, is_rebuild)
        proc = await asyncio.create_subprocess_exec(
            command, *acp_args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
            start_new_session=True,  # create process group so we can kill the whole tree
            limit=1024 * 1024,  # 1MB line buffer (default 64KB too small for large agent responses)
        )

        conn = AcpConnection(agent=agent, session_id=session_id, proc=proc, verbose=self._verbose)
        if is_rebuild:
            conn.session_reset = True
        await conn.initialize()
        await conn.session_new(cwd, profile=profile)
        return conn

    async def close(self, agent: str, session_id: str) -> None:
        key = (agent, session_id)
        conn = self._connections.pop(key, None)
        if conn:
            log.info("closing: agent=%s session=%s", agent, session_id)
            await conn.kill()
            self._save_pids()

    def remove(self, agent: str, session_id: str) -> None:
        self._connections.pop((agent, session_id), None)

    async def cleanup_idle(self, ttl_seconds: float) -> None:
        cutoff = time.time() - ttl_seconds
        stale = [k for k, c in self._connections.items() if c.last_active < cutoff]
        for key in stale:
            conn = self._connections.pop(key)
            log.info("cleanup idle: agent=%s session=%s", key[0], key[1])
            await conn.kill()

    @staticmethod
    def _mem_used_pct() -> float:
        """Return system memory usage percentage via /proc/meminfo (Linux only)."""
        try:
            info = {}
            for line in Path("/proc/meminfo").read_text().splitlines():
                parts = line.split()
                if len(parts) >= 2:
                    info[parts[0].rstrip(":")] = int(parts[1])
            total = info.get("MemTotal", 0)
            avail = info.get("MemAvailable", 0)
            if total <= 0:
                return 0.0
            return (total - avail) / total * 100
        except Exception:
            return 0.0

    async def memory_evict(self) -> int:
        """Evict idle connections when system memory exceeds threshold. Returns count evicted."""
        pct = self._mem_used_pct()
        if pct < self._memory_limit_pct:
            return 0
        evicted = 0
        while pct >= self._memory_limit_pct:
            lru = self._lru_idle()
            if not lru:
                log.warning("memory_pressure: %.0f%% used, no idle connections to evict", pct)
                break
            log.warning("memory_evict: %.0f%% used (limit %.0f%%), evicting agent=%s session=%s",
                        pct, self._memory_limit_pct, lru[0], lru[1])
            await self._evict(lru)
            evicted += 1
            pct = self._mem_used_pct()
        return evicted

    async def health_check(self) -> None:
        """Ping all idle connections; kill and remove unresponsive ones."""
        dead: list[tuple[str, str]] = []
        for key, conn in list(self._connections.items()):
            if not conn.alive:
                dead.append(key)
                continue
            if conn._busy:
                continue
            ok = await conn.ping()
            if not ok:
                dead.append(key)
        for key in dead:
            conn = self._connections.pop(key, None)
            if conn:
                log.warning("health_check: agent=%s session=%s unresponsive, killing", key[0], key[1])
                await conn.kill()

    async def shutdown(self) -> None:
        for key, conn in list(self._connections.items()):
            log.info("shutdown: killing agent=%s session=%s", key[0], key[1])
            await conn.kill()
        self._connections.clear()

    _pidfile = Path("/tmp/acp-bridge-pids")

    def _save_pids(self) -> None:
        """Persist managed subprocess PIDs to disk for ghost cleanup across restarts."""
        pids = {str(c.proc.pid) for c in self._connections.values()}
        self._pidfile.write_text("\n".join(pids) + "\n" if pids else "")

    def cleanup_ghosts(self) -> int:
        """Kill orphaned agent processes recorded by a previous Bridge run."""
        if not self._pidfile.exists():
            return 0
        own_pids = {c.proc.pid for c in self._connections.values()}
        killed = 0
        for line in self._pidfile.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            pid = int(line)
            if pid in own_pids:
                continue
            try:
                os.killpg(pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                try:
                    os.kill(pid, signal.SIGKILL)
                except (ProcessLookupError, PermissionError):
                    continue
            log.warning("ghost_cleanup: killed pid=%d", pid)
            killed += 1
        if killed:
            log.info("ghost_cleanup: killed %d orphaned processes", killed)
        self._pidfile.unlink(missing_ok=True)
        return killed

    @property
    def stats(self) -> dict:
        agents: dict[str, int] = {}
        busy = 0
        for (a, _), c in self._connections.items():
            if c.alive:
                agents[a] = agents.get(a, 0) + 1
            if c._busy:
                busy += 1
        return {"total": len(self._connections), "busy": busy, "by_agent": agents}
