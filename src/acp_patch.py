"""ACP SDK hotfixes — root cause of the 2026-08-11 "health unreachable" incident.

Two upstream defects in acp-sdk 1.0.3 combine into a busy-loop that saturates
the main thread:

1. ``MemoryStore`` shares ONE ``asyncio.Event`` across all keys: every
   ``set()`` wakes every watcher, and each wakeup re-deserializes the full
   RunData via pydantic. Measured: 50 watchers x 100 unrelated writes =
   5050 wakeups (scratch try_v0420.py part 1).
2. ``Executor._watch_for_cancellation`` loops ``while not self.task.done()``
   but only re-checks that condition after its inner ``store.watch`` iterator
   yields — which, with the shared Event, happens on every store write. A
   finished run therefore leaves a zombie watcher per historical run that
   wakes on every write, forever (until the 1h TTL evicts the RunData and
   ``watch`` raises).

Fix 1 replaces the store via the public ``create_app(store=...)`` hook — no
monkeypatching. Fix 2 wraps ``Executor.execute`` so the cancellation watcher
is cancelled as soon as the run task finishes.
"""

import asyncio
import weakref
from collections.abc import AsyncIterator


def _apply_uvicorn_loop_shim() -> None:
    """Restore ``uvicorn.config.LoopSetupType`` on uvicorn >= 0.36 (issue #21).

    acp-sdk 1.0.3 (the latest release, with no uvicorn version constraint)
    annotates ``Server.run``'s ``loop`` parameter as
    ``uvicorn.config.LoopSetupType``. uvicorn 0.36 renamed that name to
    ``LoopFactoryType`` (same ``Literal['none','auto','asyncio','uvloop']``),
    so under our pinned uvicorn 0.41.0 the name is gone. It doesn't break at
    import time (PEP 649 lazy annotations on 3.14), but any path that forces
    annotation evaluation — ``typing.get_type_hints`` /
    ``inspect.signature(..., eval_str=True)`` — raises ``AttributeError``.

    Alias the old name to the new one so those paths resolve. Guarded by
    ``hasattr`` so it is a no-op on uvicorn < 0.36 (where the original name
    still exists) and idempotent on repeat calls.

    Must run before ``acp_sdk.server`` annotations are evaluated; this module
    calls it at import time, ahead of the acp_sdk imports below.
    """
    try:
        import uvicorn.config as _uc
    except Exception:
        return
    if not hasattr(_uc, "LoopSetupType") and hasattr(_uc, "LoopFactoryType"):
        _uc.LoopSetupType = _uc.LoopFactoryType


_apply_uvicorn_loop_shim()

from acp_sdk.server.executor import Executor
from acp_sdk.server.store import MemoryStore
from acp_sdk.server.store.utils import Stringable


class PerKeyEventMemoryStore(MemoryStore):
    """MemoryStore with one wakeup Event per key instead of one global Event.

    Watchers hold the strong reference to their key's Event; the
    WeakValueDictionary drops entries automatically once the last watcher
    for a key is gone, so abandoned runs leave nothing behind.
    """

    def __init__(self, *, limit: int, ttl=None) -> None:
        super().__init__(limit=limit, ttl=ttl)
        self._key_events: weakref.WeakValueDictionary[str, asyncio.Event] = (
            weakref.WeakValueDictionary()
        )

    async def set(self, key: Stringable, value) -> None:
        await super().set(key, value)
        event = self._key_events.get(str(key))
        if event is not None:
            event.set()

    async def watch(self, key: Stringable, *, ready: asyncio.Event | None = None) -> AsyncIterator:
        k = str(key)
        # Local variable keeps the Event alive for this watcher's lifetime.
        event = self._key_events.get(k)
        if event is None:
            event = asyncio.Event()
            self._key_events[k] = event
        if ready:
            ready.set()
        while True:
            await event.wait()
            event.clear()
            yield await self.get(key)


def apply_executor_patch() -> None:
    """Cancel the per-run cancellation watcher once the run task completes.

    Idempotent; safe to call multiple times.
    """
    if getattr(Executor, "_acp_bridge_watcher_reaper", False):
        return

    original_execute = Executor.execute

    def execute(self, input, *, wait: asyncio.Event) -> None:
        original_execute(self, input, wait=wait)

        def _reap(_task) -> None:
            watcher = getattr(self, "watcher", None)
            if watcher is not None and not watcher.done():
                watcher.cancel()

        self.task.add_done_callback(_reap)

    Executor.execute = execute
    Executor._acp_bridge_watcher_reaper = True
