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


def apply_marker_gated(
    backend: ServiceBackend,
    session_id: str,
    runtime: SessionRuntime | None,
    marker: str,
    text: str,
    *,
    timeout: float,
    structured: bool = False,
    retry: bool = False,
) -> bool:
    """Gate + apply in ONE runtime.lock critical section; True = consumed.

    #694 review R2-P2 (gate conditions): kimi 0.42 speaks no structured ACP
    event for compaction, but the local notices have two STRUCTURAL
    properties the bare text prefix lacks — they only come from a kimi
    session (registry agent id or ACP agentInfo name, stamped on the
    runtime at on_ready), and they arrive OUTSIDE prompt turns, the one
    exception being a manual /compact turn whose markers are emitted
    in-turn. Anything else (another ACP agent, or agent prose that happens
    to start with the same prefix inside a normal turn) is ordinary stream
    text and falls through (False), never flipping the flag. A verified wire
    CompactionBegin/End is not prose, so ``structured`` exempts only the turn
    context condition; the kimi identity condition still applies. ``retry``
    belongs to the watcher's durable backlog: after a partial apply it forces
    the one missing notice, while ordinary duplicate signals stay coalesced.

    #694 review R3-P1 (atomicity): every gate field (kimi_agent /
    turn_open / turn_may_compact / compacting) is written under
    runtime.lock, so they are RE-READ here under the same lock that
    send_message's late gate (compaction.late_gate_blocked) and the
    turn-close hooks take. A marker thread and a send thread now serialize
    in a total order: the marker either applies before the send's critical
    section (send is refused) or after it (send wins) — the old shape
    (gate read unlocked, flag flipped under a later lock acquisition) let
    a gate-passed marker pause while a send completed and THEN flip the
    flag, a torn interleave. A marker whose gate context changed while it
    waited for the lock (its /compact turn closed) is correctly rejected.
    """
    if runtime is None:
        return False
    with runtime.lock:
        if not runtime.kimi_agent or (
            runtime.turn_open and not (runtime.turn_may_compact or structured)
        ):
            return False
        # The marker interrupts prose like a tool call does: close the
        # open stream slots so later chunks start a fresh row.
        runtime.stream.reset()
        already = not retry and (
            (runtime.compacting and marker == "start")
            or (runtime.compaction_done and marker == "done")
        )
        runtime.compacting = marker == "start"
        runtime.compacting_since = time.monotonic() if marker == "start" else None
        runtime.compaction_seen = runtime.compaction_seen or marker == "start"
        runtime.compaction_done = marker == "done"
        since = runtime.compacting_since
    if marker == "start":
        compact_timer.arm_self_clear(backend, session_id, runtime, since, timeout=timeout)
    else:
        compact_timer.cancel_self_clear(runtime)
    backend.db.update_studio_chat_session(session_id, compacting=marker == "start")
    if not already:
        # Duplicate lifecycle signal: one timeline notice per transition.
        if marker == "start":
            content: dict[str, Any] = {
                "event": "compact_start",
                "detail": "正在压缩上下文，期间发送的消息可能被静默丢弃，请等压缩完成后再发送",
            }
        elif text.strip().startswith("Compaction cancelled."):
            content = {"event": "compact_done", "detail": "上下文压缩已取消，可继续发送"}
        else:
            content = {"event": "compact_done", "detail": text.strip()}
        backend.store.append_message(session_id, "status", "system", content)
    backend.store.publish_session(session_id)
    return True
