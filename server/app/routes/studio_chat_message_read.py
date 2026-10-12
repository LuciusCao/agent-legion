"""Studio chat single-message point read (#1236).

Split from studio_chat.py (file budget); create_studio_chat_router mounts it.
The panel recalibrates in-place rows (streaming agent text, in-flight
tool_call) that sit outside the 500-row refill window: after a full
refill(0) any locally-known in-flight row is refetched by id, and an SSE
update frame for an unknown id (a row that never entered the window) is
fetched by id first, falling back to the incremental refill on failure.
"""

from typing import Annotated, Any

from fastapi import APIRouter, Depends

from server.app.auth.dependencies import enforce_scoped_workspace_binding
from server.app.routes.studio_chat_contracts import (
    StudioChatMessageRecord,
    StudioChatMessageResponse,
)
from server.app.studio_chat.service import StudioChatService


def create_studio_chat_message_read_router(service: StudioChatService) -> APIRouter:
    """Read-only point fetch; scoped run tokens stay bound to their workspace (#158)."""
    router = APIRouter()
    scoped_read = Annotated[dict[str, Any], Depends(enforce_scoped_workspace_binding)]

    @router.get(
        "/workspaces/{workspace_id}/studio-chat/sessions/{session_id}/messages/{message_id}",
        response_model=StudioChatMessageResponse,
    )
    def get_message(
        workspace_id: str, session_id: str, message_id: str, _user: scoped_read
    ) -> StudioChatMessageResponse:
        message = service.get_message(session_id, workspace_id, message_id)
        return StudioChatMessageResponse(message=StudioChatMessageRecord.model_validate(message))

    return router
