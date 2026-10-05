"""Bounded close after a visibility stamp (delete #872 / archive #924).

Split from session_close.py (file budget): close_session stays the single
generation-pinned close; this module owns the retry loop that the soft-delete
and archive paths share after stamping a row the resume claim refuses.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from server.app.studio_chat.session_close import close_session

if TYPE_CHECKING:
    from server.app.studio_chat.service import StudioChatService


# Bound on close retries after a visibility stamp (delete #872 / archive #924).
_SETTLE_ATTEMPTS = 3


def close_until_settled(
    service: StudioChatService,
    session_id: str,
    workspace_id: str,
    *,
    include_deleted: bool,
    still_wanted: Callable[[dict[str, Any] | None], bool] | None = None,
) -> None:
    """Close after a stamp that the resume claim refuses (deleted_at /
    archived_at): a resume that claimed before the stamp can still be
    mid-spawn, and close's generation pin bails when a runtime registered
    after its snapshot, so retry (bounded) until the row is closed with no
    runtime registered. A spawn whose row was closed or stamped under it
    tears itself down at the registration fence / readiness check (spawn.py),
    so the bound only has to cover the registration window."""
    for _ in range(_SETTLE_ATTEMPTS):
        if still_wanted is not None and not still_wanted(
            service.db.get_studio_chat_session(session_id)
        ):
            return
        session = close_session(
            service,
            session_id,
            workspace_id,
            include_deleted=include_deleted,
            still_wanted=still_wanted,
        )
        if session["status"] == "closed" and service.runtime(session_id) is None:
            return
