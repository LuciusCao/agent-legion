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

from server.app.studio_chat import compact_timer

if TYPE_CHECKING:
    from server.app.studio_chat.events import ServiceBackend
    from server.app.studio_chat.runtime import SessionRuntime

# Self-clear backstop: a compaction that never reports completion must not
# lock the session's send path forever (the agent could die mid-compaction).
# Enforced actively by the per-window timer armed in _apply_marker — the
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
        if marker is not None:
            _apply_marker(backend, session_id, runtime, marker, text)
            return True
    if runtime is not None and counts_as_turn_content(kind):
        with runtime.lock:
            runtime.turn_update_count += 1
    return False


def _apply_marker(
    backend: ServiceBackend,
    session_id: str,
    runtime: SessionRuntime | None,
    marker: str,
    text: str,
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
            compact_timer.arm_self_clear(
                backend, session_id, runtime, since, timeout=COMPACTING_TIMEOUT_SECONDS
            )
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


def note_ready(runtime: SessionRuntime | None) -> None:
    """Fresh process = no inherited compaction state. (The replay window is
    NOT closed here — the SDK dispatches session/load replay notifications
    asynchronously, so it stays armed until the first post-resume prompt.)"""
    if runtime is None:
        return
    compact_timer.cancel_self_clear(runtime)
    with runtime.lock:
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
        if not runtime.compacting:
            return False
        since = runtime.compacting_since
        if since is not None and time.monotonic() - since < COMPACTING_TIMEOUT_SECONDS:
            return True
        runtime.compacting = False
        runtime.compacting_since = None
    db.update_studio_chat_session(session_id, compacting=False)
    return False
