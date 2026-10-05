"""Studio chat session archive / unarchive (#924, schema v90).

Archive is the recoverable way to tidy the session list; soft delete (#872)
stays as the irreversible secondary action. Runtime semantics match *close*:

- archive stamps ``archived_at`` FIRST, then closes the runtime (run token
  revoked) — the same ordering argument as session_delete: the resume claim
  and the spawn registration fence both refuse a stamped row, so once the
  stamp lands no new runtime can come up, and the bounded close retry
  (``close_until_settled``) retires whatever runtime is or is about to be
  live;
- an archived row stays readable (GET / messages answer 200: it is hidden,
  not gone), but resume is refused with 409 until it is unarchived;
- unarchive only clears the stamp. It never spawns a runtime: the row stays
  closed and the existing resume ("continue the conversation") path brings
  it back.

Both are idempotent: archiving an archived row (or unarchiving a listed one)
answers the current row without side effects.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from server.app.studio_chat.session_settle import close_until_settled

if TYPE_CHECKING:
    from server.app.studio_chat.service import StudioChatService


def archive_session(
    service: StudioChatService, session_id: str, workspace_id: str
) -> dict[str, Any]:
    service.get_session(session_id, workspace_id)
    # The stamp is conditional; a concurrent archive that won it already
    # owns the close, but closing again is an idempotent no-op either way.
    service.db.set_studio_chat_session_archived(session_id, True)
    stamped = service.db.get_studio_chat_session(session_id) or {}
    stamp = stamped.get("archived_at")
    if stamp is not None:
        # #924 review P1: the close only proceeds while the row still carries
        # this archive stamp. A concurrent unarchive clears it under the same
        # _runtimes_lock the close writes under, so either the close lands
        # first (unarchive then restores a closed row) or the close is
        # abandoned — never a restored, live session closed afterwards.
        close_until_settled(
            service,
            session_id,
            workspace_id,
            include_deleted=False,
            still_wanted=lambda row: row is not None and row.get("archived_at") == stamp,
        )
    service.store.publish_session(session_id)
    # The real current state: closed + archived, or (an unarchive won) the
    # restored row as it stands.
    return service.get_session(session_id, workspace_id)


def unarchive_session(
    service: StudioChatService, session_id: str, workspace_id: str
) -> dict[str, Any]:
    service.get_session(session_id, workspace_id)
    # Same lock as the archive close's re-validation (session_close): an
    # archive mid-close either already wrote closed or sees the cleared stamp.
    with service._runtimes_lock:
        cleared = service.db.set_studio_chat_session_archived(session_id, False)
    if cleared:
        service.store.publish_session(session_id)
    return service.get_session(session_id, workspace_id)
