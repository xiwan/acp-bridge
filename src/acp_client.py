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
    last_active: float = field(default_factory=time.time, init=False)
    session_reset: bool = field(default=False, init=False)

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
                line = await self.proc.stderr.readline()
                if not line:
                    break
                if self.verbose:
                    log.debug("acp_stderr: %s", line.decode().rstrip()[:300])
        except Exception:
            pass

    async def _read_loop(self) -> None:
        try:
            while True:
                line = await self.proc.stdout.readline()
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
                                 "result": {"optionId": "allow_always"}}
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

    async def session_new(self, cwd: str) -> str:
        sub_id, q = self._subscribe()
        try:
            result = await self._send_request("session/new", {
                "cwd": cwd,
                "mcpServers": [],
            })
            self.acp_session_id = result["sessionId"]
            log.info("session created: acp_session=%s", self.acp_session_id)
            return self.acp_session_id
        finally:
            self._unsubscribe(sub_id)

    async def session_prompt(self, prompt: str, idle_timeout: float = 300) -> AsyncIterator[dict]:
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

    def _count_agent(self, agent: str) -> int:
        return sum(1 for (a, _) in self._connections if a == agent)

    async def get_or_create(self, agent: str, session_id: str) -> AcpConnection:
        key = (agent, session_id)
        conn = self._connections.get(key)

        if conn and conn.alive:
            return conn

        is_rebuild = conn is not None
        if conn:
            log.warning("stale connection: agent=%s session=%s, rebuilding", agent, session_id)
            self._connections.pop(key, None)

        if len(self._connections) >= self._max:
            raise PoolExhaustedError(f"global limit reached ({self._max})")
        if self._count_agent(agent) >= self._max_per_agent:
            raise PoolExhaustedError(f"per-agent limit reached for {agent} ({self._max_per_agent})")

        agent_cfg = self._config.get(agent)
        if not agent_cfg:
            raise AcpError(f"agent not found: {agent}")

        conn = await self._spawn(agent, session_id, agent_cfg, is_rebuild=is_rebuild)
        self._connections[key] = conn
        return conn

    async def _spawn(self, agent: str, session_id: str, cfg: dict, is_rebuild: bool = False) -> AcpConnection:
        command = cfg["command"]
        acp_args = cfg.get("acp_args", ["acp"])
        cwd = cfg.get("working_dir", "/tmp")

        log.info("spawning: agent=%s session=%s cmd=%s %s rebuild=%s", agent, session_id, command, acp_args, is_rebuild)
        proc = await asyncio.create_subprocess_exec(
            command, *acp_args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
            start_new_session=True,  # create process group so we can kill the whole tree
        )

        conn = AcpConnection(agent=agent, session_id=session_id, proc=proc, verbose=self._verbose)
        if is_rebuild:
            conn.session_reset = True
        await conn.initialize()
        await conn.session_new(cwd)
        return conn

    async def close(self, agent: str, session_id: str) -> None:
        key = (agent, session_id)
        conn = self._connections.pop(key, None)
        if conn:
            log.info("closing: agent=%s session=%s", agent, session_id)
            await conn.kill()

    def remove(self, agent: str, session_id: str) -> None:
        self._connections.pop((agent, session_id), None)

    async def cleanup_idle(self, ttl_seconds: float) -> None:
        cutoff = time.time() - ttl_seconds
        stale = [k for k, c in self._connections.items() if c.last_active < cutoff]
        for key in stale:
            conn = self._connections.pop(key)
            log.info("cleanup idle: agent=%s session=%s", key[0], key[1])
            await conn.kill()

    async def health_check(self) -> None:
        """Ping all idle connections; kill and remove unresponsive ones."""
        dead: list[tuple[str, str]] = []
        for key, conn in list(self._connections.items()):
            if not conn.alive:
                dead.append(key)
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

    @property
    def stats(self) -> dict:
        agents: dict[str, int] = {}
        for (a, _), c in self._connections.items():
            if c.alive:
                agents[a] = agents.get(a, 0) + 1
        return {"total": len(self._connections), "by_agent": agents}
