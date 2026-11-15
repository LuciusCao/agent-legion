"""Hidden-live-row startup recovery without hidden timeline writes (#1153)."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from server.app.studio_chat.restart_settlement import RESTART_ERROR_DETAIL

if TYPE_CHECKING:
    from server.app.studio_chat.service import StudioChatService


def reconcile_hidden_zombie_sessions(service: StudioChatService, failures: list[str]) -> None:
    """Release active-cap slots for hidden live rows without timeline writes."""
    logger = logging.getLogger(__name__)
    for row in service.db.list_zombie_studio_chat_sessions(hidden=True):
        session_id = str(row["id"])
        try:
            changed = service.db.update_studio_chat_session_if(
                session_id,
                status_in=("starting", "idle", "running", "awaiting_permission"),
                status="error",
                error_detail=RESTART_ERROR_DETAIL,
                visible_only="hidden",
                archived_at_is=row.get("archived_at"),
            )
            if not changed:
                continue
            if (runtime := service.runtime(session_id)) is not None:
                service.teardown_runtime(session_id, runtime, expected=runtime)
        except Exception:
            # #204 broad-except audit: keep the failure visible to startup while
            # other rows finish. A stamp mismatch skips work instead of hiding it.
            failures.append(session_id)
            logger.warning(
                "failed to reap hidden studio chat session %s", session_id, exc_info=True
            )
