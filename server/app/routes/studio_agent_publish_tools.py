"""Studio-agent publish-request tool endpoints (#416).

Split from studio_agent_tools.py (file budget): the request tool parks a
pending publish request; the status tool polls it. Both live on the scoped
tool surface (require_studio_agent_scope + workspace binding) like the rest
of the authoring tools. The request NEVER publishes — the human-only
confirm/cancel endpoints are in routes/studio_publish_requests.py behind
reject_studio_agent_scope (STUDIO-AGENT-001).
"""

from typing import Annotated, Any

from fastapi import APIRouter, Depends

from server.app.auth.dependencies import (
    require_studio_agent_scope,
    require_studio_agent_workspace,
)
from server.app.jobs import JobQueries
from server.app.routes.job_http import raise_job_http_error
from server.app.routes.studio_publish_request_contracts import (
    StudioAgentPublishRequestResponse,
    StudioAgentPublishRequestStatusResponse,
)
from server.app.services.job_errors import JobServiceError
from server.app.services.studio_publish_requests import StudioPublishRequestService
from server.app.settings import Settings


def create_studio_agent_publish_tools_router(job_db: JobQueries, settings: Settings) -> APIRouter:
    router = APIRouter(dependencies=[Depends(require_studio_agent_scope)])
    workspace_scoped = APIRouter(dependencies=[Depends(require_studio_agent_workspace)])

    @workspace_scoped.post(
        "/studio-agent/tools/workspaces/{workspace_id}/workflow/publish-request",
        response_model=StudioAgentPublishRequestResponse,
    )
    def request_workflow_publish(
        workspace_id: str,
        user: Annotated[dict[str, Any], Depends(require_studio_agent_scope)],
    ) -> StudioAgentPublishRequestResponse:
        """Park a pending publish request: never publishes — the human
        confirms in Studio's review dialog. The workspace's draft must pass
        the full publish validation set first (a 409 names the errors)."""
        try:
            request = StudioPublishRequestService(job_db, settings).request_publish(
                workspace_id, user
            )
        except JobServiceError as exc:
            raise_job_http_error(exc)
        return StudioAgentPublishRequestResponse.model_validate({"request": request})

    @router.get(
        "/studio-agent/tools/publish-requests/{request_id}",
        response_model=StudioAgentPublishRequestStatusResponse,
    )
    def get_publish_request_status(
        request_id: str,
        user: Annotated[dict[str, Any], Depends(require_studio_agent_scope)],
    ) -> StudioAgentPublishRequestStatusResponse:
        """Poll the outcome of a request_workflow_publish call: pending until
        the human decides; confirmed (result_revision_id set when a revision
        was produced) or rejected afterwards; expired when nobody answered
        within the TTL. Session-bound authorization lives in the service."""
        try:
            request = StudioPublishRequestService(job_db, settings).get_request_status(
                request_id, user
            )
        except JobServiceError as exc:
            raise_job_http_error(exc)
        return StudioAgentPublishRequestStatusResponse.model_validate({"request": request})

    router.include_router(workspace_scoped)
    return router
