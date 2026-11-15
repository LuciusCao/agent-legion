"""Active self-clear timer for the kimi background-compaction window (#694
review P1).

The frontend locks the session input while the compacting flag is set, so
the lazy expiry check in compaction.send_blocked alone could never fire —
a lost completion marker would dead-lock the input forever. Each window
arms one daemon timer pinned to its ``compacting_since`` generation: a
stale firing (normal completion, re-armed window, torn-down runtime)
no-ops on the generation check. Split from compaction.py (file budget); the
expiry transition lives in compact_timer_fire.py.
"""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING

from server.app.studio_chat.compact_timer_fire import fire as _fire

if TYPE_CHECKING:
    from server.app.studio_chat.events import ServiceBackend
    from server.app.studio_chat.runtime import SessionRuntime


def arm_self_clear(
    backend: ServiceBackend,
    session_id: str,
    runtime: SessionRuntime,
    armed_since: float | None,
    *,
    timeout: float,
    attempt: int = 0,
) -> None:
    """Arm the window's self-clear; supersedes any older pending timer.
    ``timeout`` is passed by the caller from compaction's module constant so
    tests can shrink it via monkeypatch."""
    timer = threading.Timer(
        timeout,
        _fire,
        args=(backend, session_id, runtime, armed_since),
        kwargs={"attempt": attempt},
    )
    timer.daemon = True
    with runtime.lock:
        old = runtime.compact_timer
        runtime.compact_timer = timer
    if old is not None:
        old.cancel()
    timer.start()


def cancel_self_clear(runtime: SessionRuntime) -> None:
    with runtime.lock:
        timer, runtime.compact_timer = runtime.compact_timer, None
    if timer is not None:
        timer.cancel()
