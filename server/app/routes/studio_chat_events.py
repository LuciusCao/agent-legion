"""Studio chat SSE stream route (split from studio_chat.py, file budget).

The per-session event stream reuses the shared JobEventManager machinery on
the session's channel. It lives on the plain (unguarded) read surface with
the scoped token's workspace binding enforced — same as the other read
endpoints (STUDIO-AGENT-001: reads are allowed for the session's own scoped
token, effecting endpoints are not).
"""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from starlette import concurrency

from server.app.auth.dependencies import enforce_scoped_workspace_binding
from server.app.events import JobEventManager
from server.app.routes.job_http import raise_job_http_error
from server.app.services.job_errors import JobServiceError
from server.app.studio_chat.channels import studio_chat_channel
from server.app.studio_chat.service import StudioChatService


def create_studio_chat_events_router(
    service: StudioChatService,
    job_event_manager: JobEventManager | None,
) -> APIRouter:
    router = APIRouter()
    scoped_read = Annotated[dict[str, Any], Depends(enforce_scoped_workspace_binding)]

    @router.get(
        "/workspaces/{workspace_id}/studio-chat/sessions/{session_id}/events",
        response_class=StreamingResponse,
        responses={200: {"content": {"text/event-stream": {}}}},
    )
    async def session_events(
        request: Request, workspace_id: str, session_id: str, _user: scoped_read
    ) -> StreamingResponse:
        if job_event_manager is None:
            raise HTTPException(status_code=503, detail="Event manager not available")
        try:
            # Synchronous DB read (pool checkout) run off the loop so a busy
            # pool cannot stall every SSE/WS heartbeat behind this lookup.
            await concurrency.run_in_threadpool(service.get_session, session_id, workspace_id)
        except JobServiceError as exc:
            raise_job_http_error(exc)
        return await job_event_manager.connect(request, studio_chat_channel(session_id))

    return router
