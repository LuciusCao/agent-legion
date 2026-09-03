from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from server.app.events import JobEventManager


def create_dashboard_events_router(
    job_event_manager: JobEventManager | None = None,
) -> APIRouter:
    router = APIRouter()

    @router.get(
        "/dashboard/events",
        response_class=StreamingResponse,
        responses={200: {"content": {"text/event-stream": {}}}},
    )
    async def dashboard_events(request: Request) -> StreamingResponse:
        if job_event_manager is None:
            raise HTTPException(status_code=503, detail="Event manager not available")
        return await job_event_manager.connect(request, "dashboard")

    return router
