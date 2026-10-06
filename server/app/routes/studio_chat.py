"""Studio chat routes (phase 3 chunk 4): workspace-scoped ACP conversation API.

Thin HTTP shell over StudioChatService — no business logic here. Mounted via
``secured()`` so every endpoint passes ``require_workspace_access`` (viewers
read, editors write, non-members 404). Effecting endpoints additionally mount
``reject_studio_agent_scope`` (STUDIO-AGENT-001) via the ``guarded``
sub-router. The SSE stream lives in studio_chat_events.py (file budget) and
reuses the shared JobEventManager machinery on a per-session channel; list
management (rename / delete / archive) lives in studio_chat_session_manage.py.
"""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException

from server.app.auth.dependencies import enforce_scoped_workspace_binding, reject_studio_agent_scope
from server.app.auth.workspace_access import require_workspace_access
from server.app.events import JobEventManager
from server.app.routes.studio_chat_config import create_studio_chat_config_router
from server.app.routes.studio_chat_context import create_studio_chat_context_router
from server.app.routes.studio_chat_contracts import (
    StudioChatAgentsResponse,
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
from server.app.routes.studio_chat_events import create_studio_chat_events_router
from server.app.routes.studio_chat_session_manage import create_studio_chat_session_manage_router
from server.app.studio_chat.service import StudioChatService


def create_studio_chat_router(
    service: StudioChatService,
    job_event_manager: JobEventManager | None = None,
) -> APIRouter:
    router = APIRouter()
    # Effecting endpoints (session lifecycle, message send, permission
    # answers) refuse studio-agent scoped tokens (STUDIO-AGENT-001): a scoped
    # token must not mint fresh tokens via create_session nor self-approve
    # its own permission prompts. Reads stay on the plain router but enforce
    # the scoped token's workspace binding (#158): a run token may only read
    # the chat data of the workspace it was minted for.
    guarded = APIRouter(dependencies=[Depends(reject_studio_agent_scope)])
    scoped_read = Annotated[dict[str, Any], Depends(enforce_scoped_workspace_binding)]

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
        session = service.create_session(workspace_id, str(user["id"]), payload.agent_id)
        if payload.title:
            session = service.rename_session(session["id"], workspace_id, payload.title)
        return StudioChatSessionResponse(session=StudioChatSessionRecord.model_validate(session))

    @router.get(
        "/workspaces/{workspace_id}/studio-chat/sessions",
        response_model=StudioChatSessionsResponse,
    )
    def list_sessions(
        workspace_id: str, _user: scoped_read, archived: bool = False
    ) -> StudioChatSessionsResponse:
        # archived=true is the archive view (#924); the default list hides
        # archived sessions.
        sessions = [
            StudioChatSessionRecord.model_validate(row)
            for row in service.list_sessions(workspace_id, archived=archived)
        ]
        return StudioChatSessionsResponse(
            sessions=sessions, retention_days=service.retention_days()
        )

    @router.get(
        "/workspaces/{workspace_id}/studio-chat/sessions/{session_id}",
        response_model=StudioChatSessionResponse,
    )
    def get_session(
        workspace_id: str, session_id: str, _user: scoped_read
    ) -> StudioChatSessionResponse:
        session = service.get_session(session_id, workspace_id)
        return StudioChatSessionResponse(session=StudioChatSessionRecord.model_validate(session))

    @guarded.delete(
        "/workspaces/{workspace_id}/studio-chat/sessions/{session_id}",
        response_model=StudioChatSessionResponse,
    )
    def close_session(workspace_id: str, session_id: str) -> StudioChatSessionResponse:
        session = service.close_session(session_id, workspace_id)
        return StudioChatSessionResponse(session=StudioChatSessionRecord.model_validate(session))

    @guarded.post(
        "/workspaces/{workspace_id}/studio-chat/sessions/{session_id}/resume",
        response_model=StudioChatSessionResponse,
    )
    def resume_session(
        workspace_id: str,
        session_id: str,
        user: Annotated[dict[str, Any], Depends(require_workspace_access)],
    ) -> StudioChatSessionResponse:
        session = service.resume_session(session_id, workspace_id, str(user["id"]))
        return StudioChatSessionResponse(session=StudioChatSessionRecord.model_validate(session))

    @router.get(
        "/workspaces/{workspace_id}/studio-chat/sessions/{session_id}/messages",
        response_model=StudioChatMessagesResponse,
    )
    def list_messages(
        workspace_id: str, session_id: str, _user: scoped_read, after_seq: int = 0
    ) -> StudioChatMessagesResponse:
        messages = service.list_messages(session_id, workspace_id, after_seq=after_seq)
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
        message = service.send_message(session_id, workspace_id, payload.text)
        return StudioChatMessageResponse(message=StudioChatMessageRecord.model_validate(message))

    @guarded.post(
        "/workspaces/{workspace_id}/studio-chat/sessions/{session_id}/cancel",
        response_model=StudioChatSessionResponse,
    )
    def cancel_turn(workspace_id: str, session_id: str) -> StudioChatSessionResponse:
        session = service.cancel(session_id, workspace_id)
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
        service.respond_permission(
            session_id,
            workspace_id,
            request_id,
            option_id=payload.option_id,
            deny=payload.deny,
        )
        return StudioChatPermissionAnswerResponse(resolved=request_id)

    # Order matters: the config router's fixed permissions/allow-all path
    # must register before guarded's permissions/{request_id} template, or
    # FastAPI would swallow the toggle as request_id="allow-all" (pinned by
    # test_allow_all_route_registers_before_permission_answer).
    router.include_router(create_studio_chat_config_router(service))
    router.include_router(create_studio_chat_context_router(service))
    router.include_router(create_studio_chat_session_manage_router(service))
    router.include_router(create_studio_chat_events_router(service, job_event_manager))
    router.include_router(guarded)
    return router
