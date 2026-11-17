"""Idempotent terminal projection for one unexpectedly exited runtime (#1153)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from uuid import uuid4

from server.app.studio_chat.background_wakeup import cancel_wakeup
from server.app.studio_chat.normal_terminal import record_normal_completion
from server.app.studio_chat.turn_watchdog import disarm_turn_watchdog

if TYPE_CHECKING:
    from server.app.studio_chat.events import ServiceBackend
    from server.app.studio_chat.runtime import SessionRuntime

PROCESS_EXIT_DETAIL = "agent process exited"
DROPPED_PROCESS_EXIT = "agent 进程已退出，排队消息未投递，请重发"
UNKNOWN_PROCESS_EXIT = (
    "agent 进程已退出，已投递消息的处理结果未知；请核对状态后手动继续，勿直接重发"
)
_LIVE = ("idle", "running", "awaiting_permission", "error")


def _content_sets(
    messages: list[dict[str, Any]],
) -> tuple[set[str], set[str], set[str], set[str], dict[str, int]]:
    resolved: set[str] = set()
    delivered: set[str] = set()
    dropped: set[str] = set()
    unknown: set[str] = set()
    completed: dict[str, int] = {}
    for message in messages:
        content = message.get("content") or {}
        if (
            message.get("kind") == "permission"
            and content.get("status") == "resolved"
            and (request_id := content.get("request_id"))
        ):
            resolved.add(str(request_id))
        if message.get("kind") != "status":
            continue
        message_id = str(content.get("message_id") or "")
        event = content.get("event")
        if event == "queued_delivered":
            delivered.add(message_id)
        elif event == "queued_dropped":
            dropped.add(message_id)
        elif event == "error" and content.get("outcome") == "unknown" and message_id:
            unknown.add(message_id)
        record_normal_completion(completed, message)
    return resolved, delivered, dropped, unknown, completed


def _append(
    backend: ServiceBackend,
    session_id: str,
    row: dict[str, Any],
    kind: str,
    role: str,
    content: dict[str, Any],
) -> dict[str, Any] | None:
    return backend.store.append_message_if_live(
        session_id,
        kind,
        role,
        content,
        _LIVE,
        error_detail_is=(
            str(row.get("error_detail") or "") if row.get("status") == "error" else None
        ),
    )


def _capture(runtime: SessionRuntime) -> None:
    runtime.exit_started = True
    runtime.exit_event_id = runtime.exit_event_id or uuid4().hex
    runtime.closed = True
    runtime.background_stop.set()
    cancel_wakeup(runtime)
    runtime.turn_open = False
    disarm_turn_watchdog(runtime)
    runtime.empty_turn_retry = None
    for pending in runtime.pending_permissions.values():
        if not pending.event.is_set():
            pending.decision = {"deny": True, "via": "process_exit"}
            pending.event.set()
    runtime.inbound_pending = 0
    if (gate := runtime.unprompted_gate) is not None:
        gate.held.clear()
        gate.front = 0


def _settle_permissions(
    backend: ServiceBackend,
    session_id: str,
    runtime: SessionRuntime,
    row: dict[str, Any],
    resolved: set[str],
) -> bool:
    for request_id, pending in list(runtime.pending_permissions.items()):
        if request_id in resolved:
            runtime.pending_permissions.pop(request_id, None)
            continue
        content = {
            "request_id": request_id,
            "status": "resolved",
            "decision": pending.decision,
        }
        if _append(backend, session_id, row, "permission", "user", content) is None:
            return False
        resolved.add(request_id)
        runtime.pending_permissions.pop(request_id, None)
    return True


def _settle_inbound(
    backend: ServiceBackend,
    session_id: str,
    runtime: SessionRuntime,
    row: dict[str, Any],
    delivered: set[str],
    dropped: set[str],
    unknown: set[str],
    completed: dict[str, int],
) -> bool:
    delivery_seq = getattr(runtime.turn_owner, "delivery_seq", 0)
    for message_id, state in list(runtime.inbound_messages.items()):
        if completed.get(message_id, 0) > delivery_seq:
            runtime.inbound_messages.pop(message_id, None)
            continue
        was_delivered = state == "delivered" or message_id in delivered
        if was_delivered and message_id not in unknown:
            content = {
                "event": "error",
                "message_id": message_id,
                "outcome": "unknown",
                "detail": UNKNOWN_PROCESS_EXIT,
            }
            if _append(backend, session_id, row, "status", "system", content) is None:
                return False
            unknown.add(message_id)
        elif not was_delivered and message_id not in dropped:
            content = {
                "event": "queued_dropped",
                "message_id": message_id,
                "detail": DROPPED_PROCESS_EXIT,
            }
            if _append(backend, session_id, row, "status", "system", content) is None:
                return False
            dropped.add(message_id)
        runtime.inbound_messages.pop(message_id, None)
    return True


def project_process_exit(backend: ServiceBackend, session_id: str, runtime: SessionRuntime) -> None:
    """Fence first, settle IDs, then expose the resumable error state.

    Runtime identity is the in-memory generation. A detached retry is allowed
    only for the generation that already started this projection; a registered
    successor or a starting/closed row always suppresses stale writes.
    """
    with runtime.lock:
        if runtime.exit_projection_done or (runtime.closed and not runtime.exit_started):
            return
        current = backend.runtime(session_id)
        if current is not runtime and (current is not None or not runtime.exit_started):
            return
        row = backend.db.get_studio_chat_session(session_id)
        if row is None or row.get("status") in ("closed", "starting"):
            runtime.exit_projection_done = True
            return
        if (
            current is None
            and row.get("status") == "error"
            and row.get("error_detail") != PROCESS_EXIT_DETAIL
        ):
            runtime.exit_projection_done = True
            return
        if not runtime.exit_started:
            _capture(runtime)
        messages = backend.db.list_studio_chat_messages(session_id)
        resolved, delivered, dropped, unknown, completed = _content_sets(messages)
        if not _settle_permissions(backend, session_id, runtime, row, resolved):
            runtime.exit_projection_done = True
            return
        if not _settle_inbound(
            backend, session_id, runtime, row, delivered, dropped, unknown, completed
        ):
            runtime.exit_projection_done = True
            return
        # A pre-existing fatal on_error (startup refusal, wedged turn, loop
        # crash) owns its original detail and single error row. This final exit
        # only finishes the generation's pending IDs; it is not a second crash.
        if row.get("status") == "error" and row.get("error_detail") != PROCESS_EXIT_DETAIL:
            runtime.exit_projection_done = True
            return
        changed = backend.db.update_studio_chat_session_if(
            session_id,
            status_not_in=("closed", "starting"),
            status="error",
            error_detail=PROCESS_EXIT_DETAIL,
        )
        if not changed:
            runtime.exit_projection_done = True
            return
        has_error = any(
            message.get("kind") == "status"
            and (content := message.get("content") or {}).get("event") == "error"
            and content.get("detail") == PROCESS_EXIT_DETAIL
            and content.get("runtime_exit_id") == runtime.exit_event_id
            and not content.get("message_id")
            for message in messages
        )
        if not has_error:
            appended = backend.store.append_message_if_live(
                session_id,
                "status",
                "system",
                {
                    "event": "error",
                    "detail": PROCESS_EXIT_DETAIL,
                    "runtime_exit_id": runtime.exit_event_id,
                },
                ("error",),
                error_detail_is=PROCESS_EXIT_DETAIL,
            )
            if appended is None:
                runtime.exit_projection_done = True
                return
        backend.store.publish_session(session_id)
        runtime.exit_projection_done = True
