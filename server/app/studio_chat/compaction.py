"""Session-level context-health signals for Studio chat (#694).

kimi 0.42 runs ``/compact`` (and auto-compaction) as a fire-and-forget
background task: the prompt returns end_turn immediately while compaction
keeps running out-of-turn. During that quiescence window the kimi engine
silently queues any new prompt, the ACP layer settles it as a degenerate
zero-content instant end_turn, and once compaction finishes the message
executes detached with its output dropped — every later message takes the
same fake-completion path and the session stays deaf until resume.

kimi speaks no structured ACP event for the compaction lifecycle; it emits
LOCAL text chunks (``emitLocalChunk``), so the markers below are a kimi-0.42
heuristic: start = "Compacting conversation context" (optionally "with
instruction: ..."), end = "Compaction completed." / "Compaction cancelled.".
This module owns the compacting flag (runtime + session row mirror + the
self-clear timer), the usage_update mirror, the send guard, and the
degenerate-turn fallback that flags an instant zero-content end_turn as
"agent never processed this".
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

from server.app.studio_chat import compact_markers, compact_timer

if TYPE_CHECKING:
    from server.app.studio_chat.events import ServiceBackend
    from server.app.studio_chat.runtime import SessionRuntime

# Self-clear backstop: a compaction that never reports completion must not
# lock the session's send path forever (the agent could die mid-compaction).
# Enforced actively by the per-window timer armed in apply_marker_gated — the
# frontend disables the input while the flag is set, so the lazy check in
# send_blocked alone could never fire (#694 review P1); the lazy check
# stays as the backstop for runtimes spawned before the timer existed.
COMPACTING_TIMEOUT_SECONDS = 600
# A turn settled as end_turn with zero session updates faster than this is
# treated as "never reached the agent" (the quiescence-window signature).
EMPTY_TURN_SECONDS = 2.0

SEND_BLOCKED_DETAIL = "正在压缩上下文，请稍后发送（长时间未完成可点「继续对话」重建会话）"
EMPTY_TURN_DETAIL = (
    "agent 未实际处理这条消息（可能在等待后台压缩完成）；请稍后重发，或点「继续对话」重建会话"
)

_COMPACT_START_PREFIX = "Compacting conversation context"
_COMPACT_DONE_PREFIXES = ("Compaction completed.", "Compaction cancelled.")
_TURN_CONTENT_KINDS = (
    "agent_message_chunk",
    "agent_thought_chunk",
    "tool_call",
    "tool_call_update",
)
# Update kinds suppressed wholesale inside the session/load replay window.
# usage_update rides along: a replayed usage is a stale historical mirror
# and overwriting the current value with it is worse than showing nothing —
# the first post-resume turn's usage_update restores a true reading (#694
# review P2-b).
_REPLAY_KINDS = ("agent_message_chunk", "agent_thought_chunk", "usage_update")


def compact_marker(text: str) -> str | None:
    """Classify a kimi local chunk: "start" / "done" / None (kimi 0.42 heuristic)."""
    stripped = text.strip()
    if stripped.startswith(_COMPACT_START_PREFIX):
        return "start"
    if any(stripped.startswith(prefix) for prefix in _COMPACT_DONE_PREFIXES):
        return "done"
    return None


def usage_record(update: dict[str, Any]) -> dict[str, Any] | None:
    """Extract the ACP usage_update mirror: used = tokens in context, size =
    context window (kimi pushes a one-shot after each turn settles)."""
    used, size = update.get("used"), update.get("size")
    if not isinstance(used, int) or not isinstance(size, int) or size <= 0:
        return None
    record: dict[str, Any] = {"used": used, "size": size}
    if isinstance(update.get("cost"), dict):
        record["cost"] = update["cost"]
    return record


def counts_as_turn_content(kind: Any) -> bool:
    """Session updates that prove the agent actually processed the turn;
    usage_update/config mirrors deliberately do not count (#694)."""
    return kind in _TURN_CONTENT_KINDS or (isinstance(kind, str) and kind.startswith("plan"))


def preprocess_update(
    backend: ServiceBackend, session_id: str, runtime: SessionRuntime | None, update: dict[str, Any]
) -> bool:
    """Session-update pre-dispatch; True = consumed (events.py must not dispatch).

    Handles the usage mirror, compaction lifecycle markers (converted into
    user-visible status messages instead of folding into the text stream),
    session/load replay suppression, and the per-turn content counter the
    degenerate-turn detector reads.
    """
    kind = update.get("sessionUpdate")
    if runtime is not None and runtime.loading and kind in _REPLAY_KINDS:
        # session/load replays history as fresh-looking updates; those
        # messages are already on the persisted timeline, so persisting them
        # again would duplicate it (#694). The replay filter runs BEFORE any
        # marker/usage classification: replayed compaction markers must not
        # rewrite the flag or append duplicate status messages on every
        # resume (#694 review P2-b). The window is armed by spawn only for a
        # load attempt and stays open until the first post-resume prompt —
        # the SDK dispatches notifications asynchronously, so an on_ready
        # boundary would race the replay.
        return True
    if kind == "usage_update":
        usage = usage_record(update)
        if usage is not None:
            backend.db.update_studio_chat_session(session_id, usage=usage)
            backend.store.publish_session(session_id)
        return True
    if kind in ("agent_message_chunk", "agent_thought_chunk"):
        text = str((update.get("content") or {}).get("text") or "")
        marker = compact_marker(text) if kind == "agent_message_chunk" else None
        # Gate + application share one critical section (#694 review R3-P1,
        # compact_markers.apply_marker_gated) so marker application and the
        # send path's late gate serialize on runtime.lock in a total order;
        # a marker-shaped chunk that fails the gate falls through as
        # ordinary stream text.
        if marker is not None and compact_markers.apply_marker_gated(
            backend, session_id, runtime, marker, text, timeout=COMPACTING_TIMEOUT_SECONDS
        ):
            return True
    if runtime is not None and counts_as_turn_content(kind):
        with runtime.lock:
            runtime.turn_update_count += 1
    return False


def flag_live_locked(runtime: SessionRuntime) -> bool:
    """Is the compaction window live? Caller must hold runtime.lock
    (send_message's turn-start critical section, #694 review R2-P1)."""
    if not runtime.compacting:
        return False
    since = runtime.compacting_since
    return since is not None and time.monotonic() - since < COMPACTING_TIMEOUT_SECONDS


def late_gate_blocked(db: Any, session_id: str, runtime: SessionRuntime, text: str) -> bool:
    """#694 review R2-P1 late re-check inside send_message's turn-start
    critical section (caller holds runtime.lock): a compaction marker
    landing after the early send_blocked gate flips the flag before the
    prompt hand-off — roll the turn claim back (guarded #158: a racing
    close owns the final state) and report blocked, exactly like the early
    refusal. ``/compact`` stays exempt, and a stale (timed-out) flag is
    cleared so the send may proceed; a LIVE flag is never cleared here."""
    live = flag_live_locked(runtime)
    if live and not text.lstrip().startswith("/compact"):
        db.update_studio_chat_session_if(session_id, status_in=("running",), status="idle")
        return True
    if runtime.compacting and not live:
        runtime.compacting = False
        runtime.compacting_since = None
        db.update_studio_chat_session(session_id, compacting=False)
    return False


def note_turn_closed(runtime: SessionRuntime | None) -> None:
    """Any turn close (end/cancel/timeout/error) re-opens the marker gate's
    turn-context condition (#694 review R2-P2)."""
    if runtime is None:
        return
    with runtime.lock:
        runtime.turn_open = False


def note_ready(
    backend: ServiceBackend,
    session_id: str,
    runtime: SessionRuntime | None,
    capabilities: dict[str, Any],
) -> None:
    """Fresh process = no inherited compaction state; also stamps the marker
    gate's kimi identity (#694 review R2-P2) from the registry agent id and
    the ACP agentInfo name. (The replay window is NOT closed here — the SDK
    dispatches session/load replay notifications asynchronously, so it
    stays armed until the first post-resume prompt.)"""
    if runtime is None:
        return
    compact_timer.cancel_self_clear(runtime)
    name = str((capabilities.get("agentInfo") or {}).get("name") or "")
    session = backend.db.get_studio_chat_session(session_id) or {}
    agent_id = str(session.get("agent_id") or "")
    with runtime.lock:
        runtime.kimi_agent = name.lower().startswith("kimi") or agent_id.lower().startswith("kimi")
        runtime.compacting = False
        runtime.compacting_since = None


def maybe_note_empty_turn(
    backend: ServiceBackend, session_id: str, stop_reason: str, *, timed_out: bool
) -> None:
    """Degenerate-turn fallback (#694): an end_turn with zero session updates
    that settles almost instantly means the prompt never reached the agent
    (the quiescence-window signature). Slash-command turns (/compact etc.)
    are local by design and legitimately produce no agent content."""
    if timed_out or stop_reason != "end_turn":
        return
    runtime = backend.runtime(session_id)
    if runtime is None:
        return
    with runtime.lock:
        started_at, updates = runtime.turn_started_at, runtime.turn_update_count
        slash = runtime.turn_slash_command
    if slash or updates > 0 or started_at is None:
        return
    if time.monotonic() - started_at >= EMPTY_TURN_SECONDS:
        return
    backend.store.append_message(
        session_id, "status", "system", {"event": "empty_turn", "detail": EMPTY_TURN_DETAIL}
    )


def send_blocked(db: Any, session_id: str, runtime: SessionRuntime, text: str) -> bool:
    """Quiescence-window send guard (#694). ``/compact`` itself is never
    intercepted (a manual compact finishes and the session continues); any
    other text is refused while the compacting flag is live. A flag older
    than COMPACTING_TIMEOUT_SECONDS self-clears (backstop to the armed
    timer, which is the primary self-clear since #694 review P1)."""
    if text.lstrip().startswith("/compact"):
        return False
    with runtime.lock:
        if flag_live_locked(runtime):
            return True
        if not runtime.compacting:
            return False
        runtime.compacting = False
        runtime.compacting_since = None
    db.update_studio_chat_session(session_id, compacting=False)
    return False
