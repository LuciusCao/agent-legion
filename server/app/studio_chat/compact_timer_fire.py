"""Expiry transition for one generation-pinned compaction timer (#694)."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from server.app.studio_chat.events import ServiceBackend
    from server.app.studio_chat.runtime import SessionRuntime

COMPACT_TIMEOUT_DETAIL = (
    "压缩等待超时（完成通知可能已丢失），已恢复输入；如下一条回复异常请点「继续对话」重建会话"
)
_CLEAR_ATTEMPTS = 4
_CLEAR_RETRY_SECONDS = 0.1
logger = logging.getLogger(__name__)


def fire(
    backend: ServiceBackend,
    session_id: str,
    runtime: SessionRuntime,
    armed_since: float | None,
    *,
    attempt: int = 0,
) -> None:
    # Identity BEFORE the lock (registry lookup takes runtimes_lock, and the
    # lock order is runtime.lock -> runtimes_lock — teardown takes them in
    # that order; this timer's registry probe therefore runs BEFORE
    # runtime.lock, never under it). A close/resume that re-homes the session
    # to a NEW runtime makes this timer's whole outcome stale: nothing is
    # ours to clear (#694 review R3-P2). The registry could still swap right
    # after this read — the conditional DB clear below is the atomic arbiter
    # for that residue.
    if backend.runtime(session_id) is not runtime:
        return
    with runtime.lock:
        if runtime.closed or not runtime.compacting or runtime.compacting_since != armed_since:
            return
        # #694 review R5-P2: the conditional clear rides the SAME critical
        # section as the generation check. The marker path
        # (compact_markers.apply_marker_gated) flips the in-memory window
        # under this lock and persists it right after, so the two sections
        # are mutually exclusive: a new window either arms before this
        # section (the generation check fails) or after it (its persist
        # lands after our clear). The row boolean alone would not
        # distinguish old/new windows; the lock order does.
        try:
            cleared = backend.db.clear_studio_chat_compacting_if_set(session_id)
        except Exception:
            # #204 broad-except audit: the clear has no usable result, so keep
            # the window and generation intact for a bounded same-generation
            # retry. Exhaustion stops the timer without inventing a clear.
            from server.app.studio_chat import compact_timer

            if attempt + 1 < _CLEAR_ATTEMPTS:
                compact_timer.arm_self_clear(
                    backend,
                    session_id,
                    runtime,
                    armed_since,
                    timeout=_CLEAR_RETRY_SECONDS,
                    attempt=attempt + 1,
                )
            else:
                runtime.compact_timer = None
            logger.warning(
                "failed to clear compacting session %s (attempt %d)",
                session_id,
                attempt + 1,
                exc_info=True,
            )
            return
        runtime.compacting = False
        runtime.compacting_since = None
        runtime.compaction_done = False
        runtime.compact_timer = None
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
            try:
                backend.store.append_message(
                    session_id,
                    "status",
                    "system",
                    {"event": "compact_timeout", "detail": COMPACT_TIMEOUT_DETAIL},
                )
                backend.store.publish_session(session_id)
            except Exception:
                # #204 broad-except audit: the row clear and memory cleanup are
                # already final. Notification commit is uncertain, so do not
                # duplicate it blindly; retain the traceback and recover input.
                logger.warning("failed to report compact timeout for %s", session_id, exc_info=True)
