"""Degenerate-turn detector for Studio chat (#694, #863).

An end_turn that settles almost instantly with zero content updates is the
signature of a prompt the agent never processed (kimi's compaction
quiescence window, #694). The same raw signal is also produced by benign
shapes, so the verdict is hardened in two ways (#863):

- Out-of-order grace: the ACP SDK resolves the prompt response before the
  last update handlers run (streaming.py), so a short reply can land its
  content AFTER on_turn_end. The verdict is therefore deferred by
  ``EMPTY_TURN_GRACE_SECONDS``: content counted for the same turn within the
  grace means the turn was fine and nothing is written. The turn identity is
  its ``turn_owner`` + ``turn_started_at`` stamp (open_turn); a newer turn
  opening inside the grace makes this verdict stale and it is dropped.
- Evidence-based wording: the "waiting for background compaction" text is
  only used for a kimi session that has actually shown compaction this
  process lifetime (live window or an accepted start marker); every other
  case — including all non-kimi agents — gets a neutral notice that claims
  no cause.

Platform-initiated turns (background-task wakeups, empty user text) and
slash commands are exempt: they carry no user message to "lose".
"""

from __future__ import annotations

import logging
import threading
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from server.app.studio_chat.events import ServiceBackend
    from server.app.studio_chat.runtime import SessionRuntime

logger = logging.getLogger(__name__)

# A turn settled as end_turn with zero content updates faster than this is a
# candidate for "never reached the agent" (the quiescence-window signature).
EMPTY_TURN_SECONDS = 2.0
# How long trailing content may lag the prompt response before the verdict.
EMPTY_TURN_GRACE_SECONDS = 1.5

# Construction seam: tests substitute a gated timer so the verdict never
# depends on wall-clock scheduling (#1118).
_timer_class = threading.Timer

# #882: a confirmed empty turn keeps its human message replayable — the
# idle-state 「继续对话」 re-delivers it once (empty_turn_retry.py).
COMPACTION_DETAIL = (
    "agent 未实际处理这条消息（本会话发生过上下文压缩，agent 可能仍在后台压缩）；"
    "请稍后点「继续对话」重新投递，如反复出现请点「＋ 新对话」"
)
NEUTRAL_DETAIL = (
    "agent 未返回任何内容就结束了这一轮，这条消息可能没有被处理；可点「继续对话」重新投递"
)


def schedule_check(
    backend: ServiceBackend, session_id: str, stop_reason: str, *, timed_out: bool
) -> None:
    """on_turn_end hook: arm the deferred verdict for an instant zero-content
    end_turn. Cheap pre-filters run now (elapsed time is measured at turn
    end, not at verdict time); only the content count is re-read later."""
    if timed_out or stop_reason != "end_turn":
        return
    runtime = backend.runtime(session_id)
    if runtime is None:
        return
    with runtime.lock:
        started_at = runtime.turn_started_at
        if runtime.turn_skip_empty_check or runtime.turn_update_count > 0 or started_at is None:
            return
        if time.monotonic() - started_at >= EMPTY_TURN_SECONDS:
            return
        timer = _timer_class(
            EMPTY_TURN_GRACE_SECONDS,
            _confirm,
            args=(backend, session_id, runtime, (runtime.turn_owner, started_at)),
        )
        timer.daemon = True
        old, runtime.empty_turn_timer = runtime.empty_turn_timer, timer
    if old is not None:
        old.cancel()
    timer.start()


def _confirm(
    backend: ServiceBackend,
    session_id: str,
    runtime: SessionRuntime,
    turn: tuple[object | None, float],
) -> None:
    # Identity BEFORE runtime.lock (same lock-order rule as compact_timer._fire).
    if backend.runtime(session_id) is not runtime:
        return
    try:
        with runtime.lock:
            if runtime.empty_turn_timer is threading.current_thread():
                runtime.empty_turn_timer = None
            owner, started_at = turn
            if (
                runtime.closed
                or runtime.turn_owner is not owner
                or runtime.turn_started_at != started_at
                or runtime.turn_update_count > 0
            ):
                return
            suspected = runtime.kimi_agent and (runtime.compacting or runtime.compaction_seen)
            source = runtime.turn_retry_source
            # Appended under the lock (compact_timer._fire precedent): a
            # new turn cannot open between the verdict and the notice, so
            # the warning never lands below the user's next message.
            backend.store.append_message(
                session_id,
                "status",
                "system",
                {
                    "event": "empty_turn",
                    "detail": COMPACTION_DETAIL if suspected else NEUTRAL_DETAIL,
                    "compaction_suspected": suspected,
                    "message_id": source[0] if source else None,
                },
            )
            # Armed only after the notice is durable: the UI offers the
            # replay from that row, the backend honours it from this slot.
            runtime.empty_turn_retry = source
    except Exception:
        # #204 broad-except audit: timer-thread advisory notice. Failure
        # means only that the warning row is missing (the turn itself already
        # settled via turn_end); an uncaught raise would just die in the
        # timer thread, so log with traceback and drop.
        logger.warning("studio chat empty_turn notice failed for %s", session_id, exc_info=True)
