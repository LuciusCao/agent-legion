"""Route-facing paged messages read for the studio chat panel (#1120 PR-3).

Split from service.py (file budget): the page-size policy and the
``has_more`` cursor contract live here; internal consumers keep using
``StudioChatService.list_messages`` (plain list, no cursor).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from server.app.jobs.queries.studio_chat_pagination import (
    STUDIO_CHAT_PAGE_SIZE,
    STUDIO_CHAT_PAGE_UP_SIZE,
)

if TYPE_CHECKING:
    from server.app.studio_chat.service import StudioChatService


def list_messages_page(
    service: StudioChatService,
    session_id: str,
    workspace_id: str,
    *,
    after_seq: int = 0,
    before_seq: int | None = None,
) -> tuple[list[dict[str, Any]], bool]:
    """Page of messages for the messages endpoint; see the accessor docstring
    for the cursor semantics. Page size is direction-dependent (server-side):
    500 for the default/after_seq path (unchanged first-screen behavior),
    100 for before_seq page-ups (see studio_chat_pagination)."""
    service.get_session(session_id, workspace_id)
    limit = STUDIO_CHAT_PAGE_UP_SIZE if before_seq is not None else STUDIO_CHAT_PAGE_SIZE
    return service.db.list_studio_chat_messages_page(
        session_id, after_seq=after_seq, before_seq=before_seq, limit=limit
    )
