"""Studio chat session list management routes (#872 rename / soft delete,
#924 archive / unarchive).

Split from studio_chat.py (file budget); create_studio_chat_router mounts it.
Every route here is effecting, so the sub-router mounts
reject_studio_agent_scope (STUDIO-AGENT-001) like the parent's ``guarded``
surface; membership/role checks come from the parent's ``secured()`` mount
(Studio chat is admin-only authoring, P4).
"""

from fastapi import APIRouter, Depends

from server.app.auth.dependencies import reject_studio_agent_scope
from server.app.routes.studio_chat_contracts import (
    StudioChatSessionDeleteResponse,
    StudioChatSessionRecord,
    StudioChatSessionResponse,
    StudioChatSessionUpdateRequest,
)
from server.app.studio_chat.service import StudioChatService


def create_studio_chat_session_manage_router(service: StudioChatService) -> APIRouter:
    router = APIRouter(dependencies=[Depends(reject_studio_agent_scope)])

    @router.patch(
        "/workspaces/{workspace_id}/studio-chat/sessions/{session_id}",
        response_model=StudioChatSessionResponse,
    )
    def rename_session(
        workspace_id: str, session_id: str, payload: StudioChatSessionUpdateRequest
    ) -> StudioChatSessionResponse:
        session = service.rename_session(session_id, workspace_id, payload.title.strip())
        return StudioChatSessionResponse(session=StudioChatSessionRecord.model_validate(session))

    # Soft delete (#872): DELETE on the session path already means *close*
    # (kept for existing clients), so removal from the list is its own verb.
    # Deleted sessions answer 404 everywhere afterwards (not 410: same shape
    # as an unknown id, no existence signal).
    @router.post(
        "/workspaces/{workspace_id}/studio-chat/sessions/{session_id}/delete",
        response_model=StudioChatSessionDeleteResponse,
    )
    def delete_session(workspace_id: str, session_id: str) -> StudioChatSessionDeleteResponse:
        service.delete_session(session_id, workspace_id)
        return StudioChatSessionDeleteResponse(deleted=session_id)

    # Archive (#924): the recoverable list cleanup, same verb style as
    # delete. Archive closes a live runtime first (close semantics, token
    # revoked); unarchive only clears the stamp — continuing goes through
    # /resume, which answers 409 while the session is archived.
    @router.post(
        "/workspaces/{workspace_id}/studio-chat/sessions/{session_id}/archive",
        response_model=StudioChatSessionResponse,
    )
    def archive_session(workspace_id: str, session_id: str) -> StudioChatSessionResponse:
        session = service.archive_session(session_id, workspace_id)
        return StudioChatSessionResponse(session=StudioChatSessionRecord.model_validate(session))

    @router.post(
        "/workspaces/{workspace_id}/studio-chat/sessions/{session_id}/unarchive",
        response_model=StudioChatSessionResponse,
    )
    def unarchive_session(workspace_id: str, session_id: str) -> StudioChatSessionResponse:
        session = service.unarchive_session(session_id, workspace_id)
        return StudioChatSessionResponse(session=StudioChatSessionRecord.model_validate(session))

    return router
