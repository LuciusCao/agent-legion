"""Studio-agent session context endpoint (schema v45).

Split from studio_agent_tools.py (file budget): the ``get_studio_context``
MCP tool's backing route. Unlike the workspace-path tool endpoints, the
session scope arrives as a path parameter and the workspace binding is
enforced inside the service (bound token + foreign session = 404).
"""

from typing import Annotated, Any

from fastapi import APIRouter, Depends

from server.app.auth.dependencies import require_studio_agent_scope
from server.app.jobs import JobQueries
from server.app.routes.job_http import raise_job_http_error
from server.app.routes.studio_agent_context_contracts import StudioChatContextResponse
from server.app.services.job_errors import JobServiceError
from server.app.services.studio_chat_context import build_session_context


def create_studio_agent_context_router(job_db: JobQueries) -> APIRouter:
    router = APIRouter(dependencies=[Depends(require_studio_agent_scope)])

    @router.get(
        "/studio-agent/tools/chat-sessions/{session_id}/context",
        response_model=StudioChatContextResponse,
    )
    def get_chat_session_context(
        session_id: str,
        user: Annotated[dict[str, Any], Depends(require_studio_agent_scope)],
    ) -> StudioChatContextResponse:
        try:
            context = build_session_context(job_db, session_id, user.get("scoped_workspace_id"))
        except JobServiceError as exc:
            raise_job_http_error(exc)
        return StudioChatContextResponse.model_validate(context)

    return router
