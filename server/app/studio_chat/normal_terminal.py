"""Linearize normal close ownership, durable terminal proof and fatal cleanup."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from server.app.studio_chat import compaction
from server.app.studio_chat.turn_lease import TurnLease

if TYPE_CHECKING:
    from server.app.studio_chat.events import ServiceBackend
    from server.app.studio_chat.runtime import SessionRuntime

logger = logging.getLogger(__name__)


class NormalCloseClaim:
    def __init__(self, lease: TurnLease | None) -> None:
        self.lease = lease
        if lease is not None:
            self.state = lease.state
            self.total_deadline = lease.total_deadline
            self.cancel_deadline = lease.cancel_deadline
            self.deadline = lease.deadline

    def rollback(self) -> None:
        lease = self.lease
        if lease is None:
            return
        with lease.handle.control_lock:
            if lease.state != "CLOSED":
                return
            lease.state = self.state
            lease.total_deadline = self.total_deadline
            lease.cancel_deadline = self.cancel_deadline
            lease.deadline = self.deadline
            lease._arm_timer_locked()

    def fail_uncertain(self, detail: str) -> bool:
        self.rollback()
        if self.lease is None:
            return False
        self.lease.fail_claim_uncertain(detail)
        return self.lease.fatal_for_prompt()


def begin_normal_close(runtime: SessionRuntime | None) -> NormalCloseClaim | None:
    lease = runtime.turn_owner if runtime is not None else None
    if not isinstance(lease, TurnLease):
        return NormalCloseClaim(None)
    handle = lease.handle
    with handle.control_lock:
        if (
            runtime is None
            or lease.state not in ("OPEN", "RESPONDED")
            or handle.active_lease is not lease
            or handle.fatal_lease is not None
            or handle._closed
            or handle._stop_requested
            or handle._transport_failure is not None
            or runtime.turn_owner is not lease
        ):
            return None
        claim = NormalCloseClaim(lease)
        lease.state = "CLOSED"
        lease.deadline = lease.total_deadline = lease.cancel_deadline = None
        lease._arm_timer_locked()
        return claim


def _latest_seq(backend: ServiceBackend, session_id: str) -> int:
    latest = backend.db.list_studio_chat_messages(session_id, limit=1)
    return int(latest[-1]["seq"]) if latest else 0


def append_normal_terminal(
    backend: ServiceBackend,
    session_id: str,
    claim: NormalCloseClaim,
    content: dict[str, Any],
) -> dict[str, Any]:
    try:
        after_seq = _latest_seq(backend, session_id)
    except Exception:
        # #204 broad-except audit: no terminal write was attempted, so restoring
        # the prior active lease and its original deadlines is safe.
        claim.rollback()
        raise
    try:
        return backend.store.append_message(session_id, "status", "system", content)
    except Exception as exc:
        # #204 broad-except audit: a post-commit exception requires exact proof
        # of this append; otherwise roll back, or fail closed if proof is unreadable.
        try:
            proof = next(
                (
                    row
                    for row in backend.db.list_studio_chat_messages_forward(
                        session_id, after_seq=after_seq
                    )
                    if row.get("kind") == "status" and row.get("content") == content
                ),
                None,
            )
        except Exception:
            # #204 broad-except audit: an unreadable post-append proof cannot
            # authorize either normal close or rollback-to-healthy; use fatal.
            logger.warning("normal terminal proof read failed for %s", session_id, exc_info=True)
            claim.fail_uncertain(f"{type(exc).__name__}: {exc}")
            raise exc from None
        if proof is None:
            claim.rollback()
            raise
        logger.warning("normal terminal append recovered for %s", session_id, exc_info=True)
        return proof


def finish_normal_terminal(
    backend: ServiceBackend,
    session_id: str,
    runtime: SessionRuntime | None,
    claim: NormalCloseClaim,
) -> None:
    try:
        compaction.note_turn_closed(runtime)
        backend.db.update_studio_chat_session_if(
            session_id, status_in=("running", "awaiting_permission"), status="idle"
        )
        backend.store.publish_session(session_id)
    except Exception as exc:
        # #204 broad-except audit: durable completion dominates M19 unknown;
        # fail the session instead of leaving a half-closed running turn without an owner.
        logger.warning("normal terminal finalization failed for %s", session_id, exc_info=True)
        if not claim.fail_uncertain(f"{type(exc).__name__}: {exc}"):
            raise


def complete_normal_terminal(
    backend: ServiceBackend,
    session_id: str,
    runtime: SessionRuntime | None,
    content: dict[str, Any],
) -> bool:
    close = begin_normal_close(runtime)
    if close is None:
        return False
    append_normal_terminal(backend, session_id, close, content)
    finish_normal_terminal(backend, session_id, runtime, close)
    return True


def normal_completion_id(message: dict[str, Any]) -> str | None:
    content = message.get("content") or {}
    message_id = str(content.get("message_id") or "")
    event = content.get("event")
    if (
        event in ("turn_end", "turn_timeout")
        and message_id
        and content.get("turn_origin") == "human"
    ) or (
        event == "error"
        and content.get("outcome") == "failed"
        and message_id
        and content.get("turn_origin") in (None, "human")
    ):
        return message_id
    return None


def record_normal_completion(completed: dict[str, int], message: dict[str, Any]) -> None:
    if completion_id := normal_completion_id(message):
        completed[completion_id] = max(completed.get(completion_id, 0), int(message["seq"]))
