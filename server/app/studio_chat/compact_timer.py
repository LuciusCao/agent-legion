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
    # Identity BEFORE the lock (registry lookup takes runtimes_lock, and the
    # lock order is runtimes_lock -> runtime.lock — teardown takes them in
    # that order, so taking runtime.lock first here would invert it). A
    # close/resume that re-homed the session to a NEW runtime makes this
    # timer's whole outcome stale: nothing is ours to clear (#694 review
    # R3-P2). The registry could still swap right after this read — the
    # conditional DB clear below is the atomic arbiter for that residue.
    if backend.runtime(session_id) is not runtime:
        return
    with runtime.lock:
        if runtime.closed or not runtime.compacting or runtime.compacting_since != armed_since:
            return
        runtime.compacting = False
        runtime.compacting_since = None
        runtime.compact_timer = None
        # #694 review R5-P2: the conditional clear rides the SAME critical
        # section as the generation check. The marker path
        # (compact_markers.apply_marker_gated) flips the in-memory window
        # under this lock and persists it right after, so the two sections
        # are mutually exclusive: a new window either arms before this
        # section (the generation check fails) or after it (its persist
        # lands after our clear). The row boolean alone would not
        # distinguish old/new windows; the lock order does.
        cleared = backend.db.clear_studio_chat_compacting_if_set(session_id)
        # #694 review R6-P2: the timeout notice also joins this section —
        # appended after release it could land AFTER a re-armed window's
        # compact_start, claiming "input recovered" while the send gate is
        # locked again. INSERT + bus publish under the lock follow the
        # store.append_stream_chunk precedent (EventBus.publish is
        # non-blocking; only non-blocking local calls here).
        if cleared:
            # The notice only rides a successful conditional clear — when the
            # row's window was already closed (resume's on_ready) the stale
            # timeout notice is dropped instead of polluting the new owner's
            # timeline (R3-P2).
            backend.store.append_message(
                session_id,
                "status",
                "system",
                {"event": "compact_timeout", "detail": COMPACT_TIMEOUT_DETAIL},
            )
            backend.store.publish_session(session_id)
