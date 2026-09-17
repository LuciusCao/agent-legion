"""Active self-clear timer for the kimi background-compaction window (#694
review P1).

The frontend locks the session input while the compacting flag is set, so
the lazy expiry check in compaction.send_blocked alone could never fire —
a lost completion marker would dead-lock the input forever. Each window
arms one daemon timer pinned to its ``compacting_since`` generation: a
stale firing (normal completion, re-armed window, torn-down runtime)
no-ops on the generation check. Split from compaction.py (file budget).
"""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from server.app.studio_chat.events import ServiceBackend
    from server.app.studio_chat.runtime import SessionRuntime

COMPACT_TIMEOUT_DETAIL = (
    "压缩等待超时（完成通知可能已丢失），已恢复输入；如下一条回复异常请点「继续对话」重建会话"
)


def arm_self_clear(
    backend: ServiceBackend,
    session_id: str,
    runtime: SessionRuntime,
    armed_since: float | None,
    *,
    timeout: float,
) -> None:
    """Arm the window's self-clear; supersedes any older pending timer.
    ``timeout`` is passed by the caller from compaction's module constant so
    tests can shrink it via monkeypatch."""
    timer = threading.Timer(timeout, _fire, args=(backend, session_id, runtime, armed_since))
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


def _fire(
    backend: ServiceBackend, session_id: str, runtime: SessionRuntime, armed_since: float | None
) -> None:
    with runtime.lock:
        if runtime.closed or not runtime.compacting or runtime.compacting_since != armed_since:
            return
        runtime.compacting = False
        runtime.compacting_since = None
        runtime.compact_timer = None
    backend.db.update_studio_chat_session(session_id, compacting=False)
    backend.store.append_message(
        session_id,
        "status",
        "system",
        {"event": "compact_timeout", "detail": COMPACT_TIMEOUT_DETAIL},
    )
    backend.store.publish_session(session_id)
