"""Human-facing endpoints for agent-initiated publish requests (#416).

Split from workflow_draft_publish.py (file budget): the pending read is the
Studio frontend's polling source for popping the WorkflowPublishReviewDialog;
confirm/cancel are the dialog's actions. All three mount
``reject_studio_agent_scope`` (STUDIO-AGENT-001) — the confirm action IS a
user publish and must be gate-equivalent to the manual publish button, so an
agent's scoped token can never resolve its own request.
"""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request

from server.app.auth.dependencies import reject_studio_agent_scope
from server.app.jobs import JobQueries
from server.app.routes.job_http import raise_job_http_error
from server.app.routes.studio_publish_request_contracts import (
    StudioPublishRequestPendingResponse,
    StudioPublishRequestResolveResponse,
)
from server.app.scheduler_wakeup import notify_schedulable_work, reload_worker_scan_entries
from server.app.services.job_errors import JobServiceError
from server.app.services.studio_publish_requests import StudioPublishRequestService
from server.app.settings import Settings


def create_studio_publish_request_router(job_db: JobQueries, settings: Settings) -> APIRouter:
    router = APIRouter(dependencies=[Depends(reject_studio_agent_scope)])

    def _service() -> StudioPublishRequestService:
        return StudioPublishRequestService(job_db, settings)

    @router.get(
        "/workspaces/{workspace_id}/workflow-drafts/publish-request",
        response_model=StudioPublishRequestPendingResponse,
    )
    def get_pending_publish_request(workspace_id: str) -> StudioPublishRequestPendingResponse:
        # Read-side guard note: reject_studio_agent_scope also guards this
        # read — a scoped token has its own status tool; the frontend poll is
        # a full-session surface.
        request = _service().get_pending(workspace_id)
        # request=None (no pending request) validates through the Optional
        # field — the poll's "nothing to show" answer.
        return StudioPublishRequestPendingResponse.model_validate({"request": request})

    @router.post(
        "/workspaces/{workspace_id}/workflow-drafts/publish-request/{request_id}/confirm",
        response_model=StudioPublishRequestResolveResponse,
    )
    def confirm_publish_request(
        workspace_id: str,
        request_id: str,
        http_request: Request,
        _user: Annotated[dict[str, Any], Depends(reject_studio_agent_scope)],
    ) -> StudioPublishRequestResolveResponse:
        try:
            request = _service().confirm(workspace_id, request_id)
        except JobServiceError as exc:
            raise_job_http_error(exc)
        # The confirm action IS a publish: replay the manual publish route's
        # post-publish hooks (worker scan reload + schedulable-work notify),
        # so a confirmed request behaves identically to the Studio button.
        reload_worker_scan_entries(http_request)
        notify_schedulable_work()
        return StudioPublishRequestResolveResponse.model_validate({"request": request})

    @router.post(
        "/workspaces/{workspace_id}/workflow-drafts/publish-request/{request_id}/cancel",
        response_model=StudioPublishRequestResolveResponse,
    )
    def cancel_publish_request(
        workspace_id: str, request_id: str
    ) -> StudioPublishRequestResolveResponse:
        try:
            request = _service().cancel(workspace_id, request_id)
        except JobServiceError as exc:
            raise_job_http_error(exc)
        return StudioPublishRequestResolveResponse.model_validate({"request": request})

    return router
