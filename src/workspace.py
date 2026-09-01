"""Pipeline workspace TTL sweeper.

Removes expired shared_cwd directories under the pipeline workdir base
(default /tmp/acp-public). Deliberately narrow blast radius:

- only descends into the five mode subdirectories, never the base itself —
  users park loose files at the top level (bonsai.txt, build/, ...)
- only removes directories named `pipeline-*` / `conv-*`
- skips workspaces of running pipelines (caller passes `active`)
- a directory is expired only if NO file in its tree is newer than the TTL
  cutoff — directory mtime alone lies when agents write into subdirs
"""

import logging
import os
import shutil
import time
from pathlib import Path

log = logging.getLogger("acp-bridge.workspace")

MODES = ("sequence", "parallel", "race", "random", "conversation")
PREFIXES = ("pipeline-", "conv-")

_MIN_SWEEP_INTERVAL = 30 * 60  # cleanup_loop ticks every 60s; don't scan disk each tick
_last_sweep = 0.0


def _newest_mtime(root: Path, cutoff: float) -> float:
    """Newest mtime in the tree, short-circuiting once it exceeds cutoff."""
    try:
        newest = root.stat().st_mtime
    except OSError:
        return cutoff + 1  # can't stat → treat as fresh, never delete blind
    if newest > cutoff:
        return newest
    for dirpath, _dirs, files in os.walk(root):
        for name in files:
            try:
                m = os.stat(os.path.join(dirpath, name)).st_mtime
            except OSError:
                continue
            if m > newest:
                newest = m
                if newest > cutoff:
                    return newest
    return newest


def sweep(
    base: str, ttl_seconds: float, active: set[str] | None = None, throttle: bool = True
) -> int:
    """Remove expired pipeline workspaces. Returns count removed.

    Synchronous (walks disk) — call via asyncio.to_thread from async code.
    `throttle=True` makes repeat calls within 30 min no-ops.
    """
    global _last_sweep
    if ttl_seconds <= 0 or not base:
        return 0
    now = time.time()
    if throttle and now - _last_sweep < _MIN_SWEEP_INTERVAL:
        return 0
    _last_sweep = now

    active = active or set()
    cutoff = now - ttl_seconds
    removed = 0
    for mode in MODES:
        mode_dir = Path(base) / mode
        if not mode_dir.is_dir():
            continue
        try:
            entries = list(mode_dir.iterdir())
        except OSError:
            continue
        for d in entries:
            if not d.is_dir() or not d.name.startswith(PREFIXES):
                continue
            if str(d) in active:
                continue
            if _newest_mtime(d, cutoff) > cutoff:
                continue
            shutil.rmtree(d, ignore_errors=True)
            removed += 1
            log.info("workspace_swept: dir=%s age>%dh", d, int(ttl_seconds / 3600))
    if removed:
        log.info("workspace_sweep: base=%s removed=%d", base, removed)
    return removed
