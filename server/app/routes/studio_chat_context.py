"""Studio chat context route (schema v45): human-side canvas state push.

Split from studio_chat.py (file budget); create_studio_chat_router mounts it.
The route lives on the guarded surface (reject_studio_agent_scope,
STUDIO-AGENT-001): an agent must not rewrite the context it later reads back
through get_studio_context.
"""

from fastapi import APIRouter, Depends

from server.app.auth.dependencies import reject_studio_agent_scope
from server.app.routes.job_http import raise_job_http_error
from server.app.routes.studio_chat_contracts import (
    StudioChatContextUpdateRequest,
    StudioChatSessionRecord,
    StudioChatSessionResponse,
)
from server.app.services.job_errors import JobServiceError
from server.app.studio_chat.service import StudioChatService


def create_studio_chat_context_router(service: StudioChatService) -> APIRouter:
    router = APIRouter(dependencies=[Depends(reject_studio_agent_scope)])

    @router.put(
        "/workspaces/{workspace_id}/studio-chat/sessions/{session_id}/context",
        response_model=StudioChatSessionResponse,
    )
    def update_context(
        workspace_id: str, session_id: str, payload: StudioChatContextUpdateRequest
    ) -> StudioChatSessionResponse:
        try:
            # Partial update: selected_node_key writes only when the field is
            # present (explicit null still clears); the in-process draft
            # mirror updates only on a non-null draft_yaml.
            session = service.get_session(session_id, workspace_id)
            if "selected_node_key" in payload.model_fields_set:
                session = service.set_selected_node(
                    session_id, workspace_id, payload.selected_node_key
                )
            if payload.draft_yaml is not None:
                session = service.set_draft_yaml(session_id, workspace_id, payload.draft_yaml)
        except JobServiceError as exc:
            raise_job_http_error(exc)
        return StudioChatSessionResponse(session=StudioChatSessionRecord.model_validate(session))

    return router
