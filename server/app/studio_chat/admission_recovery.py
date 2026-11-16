"""Durable proof and conservative-fatal recovery for turn admission."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from server.app.auth.sessions import hash_token
from server.app.jobs.queries.studio_chat_admission import StudioChatAdmissionRejected

if TYPE_CHECKING:
    from server.app.studio_chat.runtime import SessionRuntime
    from server.app.studio_chat.service import StudioChatService
    from server.app.studio_chat.turn_lease import TurnLease

logger = logging.getLogger(__name__)


def latest_message_seq(service: StudioChatService, session_id: str) -> int:
    latest = service.db.list_studio_chat_messages(session_id, limit=1)
    return int(latest[-1]["seq"]) if latest else 0


def session_claim_status(service: StudioChatService, session_id: str) -> bool | None:
    """True=claimed, False=proven idle/unclaimed, None=commit outcome unknown."""
    try:
        row = service.db.get_studio_chat_session(session_id)
    except Exception:
        # #204 broad-except audit: a missing proof read cannot authorize abort;
        # the caller keeps the original exception and chooses conservative fatal.
        logger.warning("studio claim status proof failed for %s", session_id, exc_info=True)
        return None
    if row is None:
        return None
    if row.get("status") == "running":
        return True
    return False if row.get("status") == "idle" else None


def status_claim_proof(
    service: StudioChatService, session_id: str, message_id: str, event: str
) -> tuple[dict[str, Any] | None, bool | None]:
    try:
        proof = next(
            (
                message
                for message in service.db.list_studio_chat_messages(session_id)
                if message.get("kind") == "status"
                and (content := message.get("content") or {}).get("event") == event
                and content.get("message_id") == message_id
            ),
            None,
        )
    except Exception:
        # #204 broad-except audit: durable delivery cannot be disproved by a
        # failed read; None selects fatal instead of a false drop/abort.
        logger.warning("studio delivery proof read failed for %s", session_id, exc_info=True)
        return None, None
    if proof is not None:
        return proof, True
    return None, session_claim_status(service, session_id)


def direct_claim_proof(
    service: StudioChatService, session_id: str, text: str, after_seq: int
) -> tuple[dict[str, Any] | None, bool | None]:
    try:
        message = next(
            (
                row
                for row in service.db.list_studio_chat_messages_forward(
                    session_id, after_seq=after_seq
                )
                if row.get("kind") == "text"
                and row.get("role") == "user"
                and not (row.get("content") or {}).get("queued")
                and (row.get("content") or {}).get("text") == text
            ),
            None,
        )
    except Exception:
        # #204 broad-except audit: direct acceptance is durable before the user
        # row can be returned; an unreadable row is unknown, never a false abort.
        logger.warning("direct delivery proof read failed for %s", session_id, exc_info=True)
        return None, None
    if message is not None:
        return message, True
    return None, session_claim_status(service, session_id)


def fail_accepted_turn(
    runtime: SessionRuntime,
    owner: TurnLease,
    exc: Exception,
    *,
    text: str | None = None,
    message_id: str | None = None,
    prompt: str | None = None,
) -> None:
    if message_id is not None and text is not None and prompt is not None:
        runtime.turn_owner = owner
        runtime.turn_retry_source = (message_id, text, prompt)
        runtime.inbound_messages[message_id] = "delivered"
    owner.fail_claim_uncertain(f"{type(exc).__name__}: {exc}")


def claim_direct_message(
    service: StudioChatService,
    session_id: str,
    runtime: SessionRuntime,
    text: str,
    after_seq: int,
    owner: TurnLease,
) -> dict[str, Any]:
    try:
        return service.db.accept_studio_chat_message(session_id, hash_token(runtime.token), text)
    except StudioChatAdmissionRejected:
        owner.abort()
        raise
    except Exception as exc:
        # #204 broad-except audit: recover only an exact post-baseline durable
        # user row; proven idle aborts, while every other outcome stays fatal.
        message, status = direct_claim_proof(service, session_id, text, after_seq)
        if message is None:
            if status is False:
                owner.abort()
            else:
                logger.warning(
                    "direct chat claim outcome unknown for %s", session_id, exc_info=True
                )
                owner.fail_claim_uncertain(f"{type(exc).__name__}: {exc}")
            raise
        logger.warning(
            "direct chat claim recovered from durable proof for %s", session_id, exc_info=True
        )
        return message


def claim_retry_notice(
    service: StudioChatService,
    session_id: str,
    runtime: SessionRuntime,
    message_id: str,
    text: str,
    prompt: str,
    notice: dict[str, Any],
    owner: TurnLease,
) -> dict[str, Any]:
    try:
        return service.db.claim_studio_chat_turn_with_token(
            session_id, hash_token(runtime.token), notice
        )
    except StudioChatAdmissionRejected:
        owner.abort()
        raise
    except Exception as exc:
        # #204 broad-except audit: only the transaction's own durable retry
        # proof can recover; proven idle aborts, uncertainty is fatal.
        row, status = status_claim_proof(service, session_id, message_id, "empty_turn_retry")
        if row is not None:
            logger.warning("empty-turn claim recovered for %s", session_id, exc_info=True)
            return row
        if status is False:
            owner.abort()
        else:
            fail_accepted_turn(runtime, owner, exc, text=text, message_id=message_id, prompt=prompt)
        raise
