"""Studio chat session soft delete (#872, schema v89).

Ordering: stamp ``deleted_at`` FIRST, then close. The stamp is what makes
the row invisible (list filter, public 404) and what the resume claim
refuses, so once it lands no new runtime can be claimed for the row; the
close that follows retires whatever runtime is (or is about to be) live.
Closing first would leave a window where a resume re-opens the row between
the close and the stamp, leaving a live agent subprocess on a deleted row.

A resume that claimed before the stamp can still be mid-spawn: the bounded
close retry lives in ``session_close.close_until_settled`` (shared with the
archive path, #924).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from server.app.services.job_errors import NotFoundError
from server.app.studio_chat.session_settle import close_until_settled

if TYPE_CHECKING:
    from server.app.studio_chat.service import StudioChatService


def delete_session(service: StudioChatService, session_id: str, workspace_id: str) -> None:
    service.get_session(session_id, workspace_id)
    if not service.db.mark_studio_chat_session_deleted(session_id):
        # A concurrent delete won the stamp: same answer as an unknown id.
        raise NotFoundError("Chat session not found")
    close_until_settled(service, session_id, workspace_id, include_deleted=True)
