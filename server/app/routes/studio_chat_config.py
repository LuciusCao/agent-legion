"""Studio chat session config routes (#368): the session's control switches.

Split from studio_chat.py (file budget); create_studio_chat_router mounts it.
Two independent layers live side by side here — the platform-side allow-all
permission switch (studio policy, enforced by the backend) and the agent-side
mode / config option switches (ACP protocol, agent self-restraint); neither
setting rewrites the other. All three live on the guarded surface
(reject_studio_agent_scope, STUDIO-AGENT-001): a scoped token must not
self-approve its own permissions nor steer its own model/thinking level.
"""

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field

from server.app.auth.dependencies import reject_studio_agent_scope
from server.app.routes.job_http import raise_job_http_error
from server.app.routes.studio_chat_contracts import (
    StudioChatSessionRecord,
    StudioChatSessionResponse,
)
from server.app.services.job_errors import JobServiceError
from server.app.studio_chat.service import StudioChatService
from server.app.studio_chat.session_config import set_session_config_option, set_session_mode


class StudioChatAllowAllRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool


class StudioChatSetModeRequest(BaseModel):
    """Switch the agent-side session mode; the mode must be in the session's
    advertised availableModes (server-side whitelist)."""

    model_config = ConfigDict(extra="forbid")

    mode_id: str = Field(min_length=1)


class StudioChatSetConfigOptionRequest(BaseModel):
    """Set one agent-side config option (select type only); the id and value
    must be in the session's advertised configOptions."""

    model_config = ConfigDict(extra="forbid")

    config_id: str = Field(min_length=1)
    value: str = Field(min_length=1)


def create_studio_chat_config_router(service: StudioChatService) -> APIRouter:
    router = APIRouter(dependencies=[Depends(reject_studio_agent_scope)])

    def _session_response(session: dict) -> StudioChatSessionResponse:
        return StudioChatSessionResponse(session=StudioChatSessionRecord.model_validate(session))

    @router.post(
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
        return _session_response(session)

    @router.post(
        "/workspaces/{workspace_id}/studio-chat/sessions/{session_id}/mode",
        response_model=StudioChatSessionResponse,
    )
    def set_mode(
        workspace_id: str, session_id: str, payload: StudioChatSetModeRequest
    ) -> StudioChatSessionResponse:
        try:
            session = set_session_mode(service, session_id, workspace_id, payload.mode_id)
        except JobServiceError as exc:
            raise_job_http_error(exc)
        return _session_response(session)

    @router.post(
        "/workspaces/{workspace_id}/studio-chat/sessions/{session_id}/config-options",
        response_model=StudioChatSessionResponse,
    )
    def set_config_option(
        workspace_id: str, session_id: str, payload: StudioChatSetConfigOptionRequest
    ) -> StudioChatSessionResponse:
        try:
            session = set_session_config_option(
                service, session_id, workspace_id, payload.config_id, payload.value
            )
        except JobServiceError as exc:
            raise_job_http_error(exc)
        return _session_response(session)

    return router
