import asyncio
from functools import partial
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse

from server.app.auth.workspace_access import require_workspace_access
from server.app.auth.workspace_visibility import workspace_visibility_scope
from server.app.events import JobEventManager
from server.app.events.dashboard_visibility import DashboardStatsFilter
from server.app.services.workspace_configuration import WorkspaceConfigurationService


def create_dashboard_events_router(
    service: WorkspaceConfigurationService,
    job_event_manager: JobEventManager | None = None,
) -> APIRouter:
    router = APIRouter()

    @router.get(
        "/dashboard/events",
        response_class=StreamingResponse,
        responses={200: {"content": {"text/event-stream": {}}}},
    )
    async def dashboard_events(
        request: Request,
        user: Annotated[dict[str, Any], Depends(require_workspace_access)],
    ) -> StreamingResponse:
        if job_event_manager is None:
            raise HTTPException(status_code=503, detail="Event manager not available")
        # #881: the dashboard channel is one broadcast; a restricted identity
        # only receives stats for the workspaces it may list (#711's rule).
        # The visible set is resolved here — every (re)connect recomputes it —
        # and cached on the connection by the filter.
        member_user_id, bound_workspace_id = workspace_visibility_scope(user)
        resolve = partial(
            service.visible_workspace_ids,
            member_user_id=member_user_id,
            bound_workspace_id=bound_workspace_id,
        )
        visible = await asyncio.to_thread(resolve)
        payload_filter = None
        if visible is not None:
            payload_filter = DashboardStatsFilter(lambda: resolve() or frozenset(), visible)
        return await job_event_manager.connect(request, "dashboard", payload_filter=payload_filter)

    return router
