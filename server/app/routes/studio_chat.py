"""Studio chat routes (phase 3 chunk 4): workspace-scoped ACP conversation API.

Thin HTTP shell over StudioChatService — no business logic here. Mounted via
``secured()`` so every endpoint passes ``require_workspace_access`` (viewers
read, editors write, non-members 404). Effecting endpoints additionally mount
``reject_studio_agent_scope`` (STUDIO-AGENT-001) via the ``guarded``
sub-router. The SSE stream reuses the shared JobEventManager machinery on a
per-session channel.
"""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse

from server.app.auth.dependencies import reject_studio_agent_scope
from server.app.auth.workspace_access import require_workspace_access
from server.app.events import JobEventManager
from server.app.routes.job_http import raise_job_http_error
from server.app.routes.studio_chat_contracts import (
    StudioChatAgentsResponse,
    StudioChatAllowAllRequest,
    StudioChatMessageCreateRequest,
    StudioChatMessageRecord,
    StudioChatMessageResponse,
    StudioChatMessagesResponse,
    StudioChatPermissionAnswerRequest,
    StudioChatPermissionAnswerResponse,
    StudioChatSessionCreateRequest,
    StudioChatSessionRecord,
    StudioChatSessionResponse,
    StudioChatSessionsResponse,
)
from server.app.services.job_errors import JobServiceError
from server.app.studio_chat.service import StudioChatService, studio_chat_channel


def create_studio_chat_router(
    service: StudioChatService,
    job_event_manager: JobEventManager | None = None,
) -> APIRouter:
    router = APIRouter()
    # Effecting endpoints (session lifecycle, message send, permission
    # answers) refuse studio-agent scoped tokens (STUDIO-AGENT-001): a scoped
    # token must not mint fresh tokens via create_session nor self-approve
    # its own permission prompts. Reads stay on the plain router.
    guarded = APIRouter(dependencies=[Depends(reject_studio_agent_scope)])

    @router.get(
        "/workspaces/{workspace_id}/studio-chat/agents",
        response_model=StudioChatAgentsResponse,
    )
    def list_agents(workspace_id: str) -> StudioChatAgentsResponse:
        return StudioChatAgentsResponse.model_validate({"agents": service.list_available_agents()})

    @guarded.post(
        "/workspaces/{workspace_id}/studio-chat/sessions",
        response_model=StudioChatSessionResponse,
    )
    def create_session(
        workspace_id: str,
        payload: StudioChatSessionCreateRequest,
        user: Annotated[dict[str, Any], Depends(require_workspace_access)],
    ) -> StudioChatSessionResponse:
        try:
            session = service.create_session(workspace_id, str(user["id"]), payload.agent_id)
            if payload.title:
                session = service.rename_session(session["id"], workspace_id, payload.title)
        except JobServiceError as exc:
            raise_job_http_error(exc)
        return StudioChatSessionResponse(session=StudioChatSessionRecord.model_validate(session))

    @router.get(
        "/workspaces/{workspace_id}/studio-chat/sessions",
        response_model=StudioChatSessionsResponse,
    )
    def list_sessions(workspace_id: str) -> StudioChatSessionsResponse:
        return StudioChatSessionsResponse(
            sessions=[
                StudioChatSessionRecord.model_validate(row)
                for row in service.list_sessions(workspace_id)
            ]
        )

    @router.get(
        "/workspaces/{workspace_id}/studio-chat/sessions/{session_id}",
        response_model=StudioChatSessionResponse,
    )
    def get_session(workspace_id: str, session_id: str) -> StudioChatSessionResponse:
        try:
            session = service.get_session(session_id, workspace_id)
        except JobServiceError as exc:
            raise_job_http_error(exc)
        return StudioChatSessionResponse(session=StudioChatSessionRecord.model_validate(session))

    @guarded.delete(
        "/workspaces/{workspace_id}/studio-chat/sessions/{session_id}",
        response_model=StudioChatSessionResponse,
    )
    def close_session(workspace_id: str, session_id: str) -> StudioChatSessionResponse:
        try:
            session = service.close_session(session_id, workspace_id)
        except JobServiceError as exc:
            raise_job_http_error(exc)
        return StudioChatSessionResponse(session=StudioChatSessionRecord.model_validate(session))

    @router.get(
        "/workspaces/{workspace_id}/studio-chat/sessions/{session_id}/messages",
        response_model=StudioChatMessagesResponse,
    )
    def list_messages(
        workspace_id: str, session_id: str, after_seq: int = 0
    ) -> StudioChatMessagesResponse:
        try:
            messages = service.list_messages(session_id, workspace_id, after_seq=after_seq)
        except JobServiceError as exc:
            raise_job_http_error(exc)
        return StudioChatMessagesResponse(
            messages=[StudioChatMessageRecord.model_validate(row) for row in messages]
        )

    @guarded.post(
        "/workspaces/{workspace_id}/studio-chat/sessions/{session_id}/messages",
        response_model=StudioChatMessageResponse,
    )
    def send_message(
        workspace_id: str, session_id: str, payload: StudioChatMessageCreateRequest
    ) -> StudioChatMessageResponse:
        try:
            message = service.send_message(session_id, workspace_id, payload.text)
        except JobServiceError as exc:
            raise_job_http_error(exc)
        return StudioChatMessageResponse(message=StudioChatMessageRecord.model_validate(message))

    @router.get(
        "/workspaces/{workspace_id}/studio-chat/sessions/{session_id}/events",
        response_class=StreamingResponse,
        responses={200: {"content": {"text/event-stream": {}}}},
    )
    async def session_events(
        request: Request, workspace_id: str, session_id: str
    ) -> StreamingResponse:
        if job_event_manager is None:
            raise HTTPException(status_code=503, detail="Event manager not available")
        try:
            service.get_session(session_id, workspace_id)
        except JobServiceError as exc:
            raise_job_http_error(exc)
        return await job_event_manager.connect(request, studio_chat_channel(session_id))

    @guarded.post(
        "/workspaces/{workspace_id}/studio-chat/sessions/{session_id}/cancel",
        response_model=StudioChatSessionResponse,
    )
    def cancel_turn(workspace_id: str, session_id: str) -> StudioChatSessionResponse:
        try:
            session = service.cancel(session_id, workspace_id)
        except JobServiceError as exc:
            raise_job_http_error(exc)
        return StudioChatSessionResponse(session=StudioChatSessionRecord.model_validate(session))

    @guarded.post(
        "/workspaces/{workspace_id}/studio-chat/sessions/{session_id}/permissions/allow-all",
        response_model=StudioChatSessionResponse,
    )
    def set_allow_all(
        workspace_id: str, session_id: str, payload: StudioChatAllowAllRequest
    ) -> StudioChatSessionResponse:
        try:
            session = service.set_allow_all_permissions(session_id, workspace_id, payload.enabled)
        except JobServiceError as exc:
            raise_job_http_error(exc)
        return StudioChatSessionResponse(session=StudioChatSessionRecord.model_validate(session))

    @guarded.post(
        "/workspaces/{workspace_id}/studio-chat/sessions/{session_id}/permissions/{request_id}",
        response_model=StudioChatPermissionAnswerResponse,
    )
    def answer_permission(
        workspace_id: str,
        session_id: str,
        request_id: str,
        payload: StudioChatPermissionAnswerRequest,
    ) -> StudioChatPermissionAnswerResponse:
        if not payload.deny and not payload.option_id:
            raise HTTPException(status_code=422, detail="option_id is required unless deny=true")
        try:
            service.respond_permission(
                session_id,
                workspace_id,
                request_id,
                option_id=payload.option_id,
                deny=payload.deny,
            )
        except JobServiceError as exc:
            raise_job_http_error(exc)
        return StudioChatPermissionAnswerResponse(resolved=request_id)

    router.include_router(guarded)
    return router
