from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from server.app.events import JobEventManager
from server.app.routes.job_http import require_workflows_enabled
from server.app.settings import Settings


def create_dashboard_events_router(
    settings: Settings,
    job_event_manager: JobEventManager | None = None,
) -> APIRouter:
    router = APIRouter()

    @router.get(
        "/dashboard/events",
        response_class=StreamingResponse,
        responses={200: {"content": {"text/event-stream": {}}}},
    )
    async def dashboard_events(request: Request) -> StreamingResponse:
        require_workflows_enabled(settings)
        if job_event_manager is None:
            raise HTTPException(status_code=503, detail="Event manager not available")
        return await job_event_manager.connect(request, "dashboard")

    return router
