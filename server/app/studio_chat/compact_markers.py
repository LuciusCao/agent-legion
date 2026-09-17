"""Compaction lifecycle marker application for Studio chat (#694).

The marker TEXT heuristic lives in compaction.compact_marker; this module
owns the gate that decides whether a matching chunk is really a kimi local
compaction notice (review R2-P2) and the side effects of accepting one
(flag mirror, stream-slot reset, user-visible status message, self-clear
timer). Split from compaction.py (file budget).
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

from server.app.studio_chat import compact_timer

if TYPE_CHECKING:
    from server.app.studio_chat.events import ServiceBackend
    from server.app.studio_chat.runtime import SessionRuntime


def marker_gate_open(runtime: SessionRuntime | None) -> bool:
    """Is the session allowed to treat a marker-shaped chunk as a compaction
    notice? (#694 review R2-P2)

    kimi 0.42 speaks no structured ACP event for compaction, but the local
    notices have two STRUCTURAL properties the bare text prefix lacks: they
    only come from a kimi session (registry agent id or ACP agentInfo name,
    stamped on the runtime at on_ready), and they arrive OUTSIDE prompt
    turns — the one exception is a manual /compact turn, whose markers are
    emitted in-turn. Anything else (another ACP agent, or agent prose that
    happens to start with the same prefix inside a normal turn) is ordinary
    stream text and must never flip the flag or lock the input. The fields
    are single-word stores written under runtime.lock; a bare read can only
    observe a whole old/new value, which the gate tolerates.
    """
    if runtime is None or not runtime.kimi_agent:
        return False
    return not runtime.turn_open or runtime.turn_may_compact


def apply_marker(
    backend: ServiceBackend,
    session_id: str,
    runtime: SessionRuntime | None,
    marker: str,
    text: str,
    *,
    timeout: float,
) -> None:
    already = False
    if runtime is not None:
        with runtime.lock:
            # The marker interrupts prose like a tool call does: close the
            # open stream slots so later chunks start a fresh row.
            runtime.stream.reset()
            already = runtime.compacting and marker == "start"
            runtime.compacting = marker == "start"
            runtime.compacting_since = time.monotonic() if marker == "start" else None
            since = runtime.compacting_since
        if marker == "start":
            compact_timer.arm_self_clear(backend, session_id, runtime, since, timeout=timeout)
        else:
            compact_timer.cancel_self_clear(runtime)
    backend.db.update_studio_chat_session(session_id, compacting=marker == "start")
    if already:
        # Duplicate start marker (re-arm only): one timeline notice is enough.
        return
    if marker == "start":
        content: dict[str, Any] = {
            "event": "compact_start",
            "detail": "正在压缩上下文，期间发送的消息可能被静默丢弃，请等压缩完成后再发送",
        }
    elif text.strip().startswith("Compaction cancelled."):
        content = {"event": "compact_done", "detail": "上下文压缩已取消，可继续发送"}
    else:
        # kimi's completion chunk carries the token stats lines — keep them.
        content = {"event": "compact_done", "detail": text.strip()}
    backend.store.append_message(session_id, "status", "system", content)
    backend.store.publish_session(session_id)
