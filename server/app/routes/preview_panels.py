"""Workspace preview panel routes (schema v71, issue #328).

Mounted under the secured() surface (require_workspace_access): the published
bundle read serves every workspace member's job detail page, so it stays at
member level. Authoring reads (draft state) and the effecting writes
(publish/archive) carry ``require_studio_authoring`` — admin sessions or
scoped tokens — and the writes additionally mount ``reject_studio_agent_scope``
(STUDIO-AGENT-001): the studio agent drafts via the tool surface, a human
publishes here. Same auth split as the workflow draft store.
"""

from fastapi import APIRouter, Depends

from server.app.auth.dependencies import reject_studio_agent_scope
from server.app.auth.studio_authoring import require_studio_authoring
from server.app.jobs import JobQueries
from server.app.routes.job_http import raise_job_http_error
from server.app.routes.studio_agent_preview_contracts import (
    PreviewPanelPublishedResponse,
    PreviewPanelStateResponse,
    PreviewPanelVersionResponse,
)
from server.app.services.job_errors import JobServiceError, NotFoundError
from server.app.services.preview_panels import PreviewPanelService
from server.app.settings import Settings

_AUTHORING = Depends(require_studio_authoring)
_EFFECTING = Depends(reject_studio_agent_scope)


def create_preview_panels_router(job_db: JobQueries, settings: Settings) -> APIRouter:
    del settings  # no feature gate: the panel serves job detail pages directly
    router = APIRouter()

    def _service(workspace_id: str) -> PreviewPanelService:
        if job_db.get_workspace(workspace_id) is None:
            raise_job_http_error(NotFoundError("Workspace not found"))
        return PreviewPanelService(job_db)

    @router.get(
        "/workspaces/{workspace_id}/preview-panel/published",
        response_model=PreviewPanelPublishedResponse,
    )
    def get_published(workspace_id: str) -> PreviewPanelPublishedResponse:
        """Published bundle for the job detail iframe host; null = fallback."""
        row = _service(workspace_id).get_published(workspace_id)
        return PreviewPanelPublishedResponse(
            published=PreviewPanelVersionResponse.model_validate(row) if row else None
        )

    @router.get(
        "/workspaces/{workspace_id}/preview-panel",
        response_model=PreviewPanelStateResponse,
        dependencies=[_AUTHORING],
    )
    def get_state(workspace_id: str) -> PreviewPanelStateResponse:
        return PreviewPanelStateResponse.model_validate(
            _service(workspace_id).get_state(workspace_id)
        )

    @router.post(
        "/workspaces/{workspace_id}/preview-panel/publish",
        response_model=PreviewPanelVersionResponse,
        dependencies=[_AUTHORING, _EFFECTING],
    )
    def publish(workspace_id: str) -> PreviewPanelVersionResponse:
        try:
            row = _service(workspace_id).publish(workspace_id)
        except JobServiceError as exc:
            raise_job_http_error(exc)
        return PreviewPanelVersionResponse.model_validate(row)

    @router.post(
        "/workspaces/{workspace_id}/preview-panel/archive",
        response_model=PreviewPanelStateResponse,
        dependencies=[_AUTHORING, _EFFECTING],
    )
    def archive(workspace_id: str) -> PreviewPanelStateResponse:
        """Human "reset to the built-in fallback": archive every version."""
        _service(workspace_id).archive_all(workspace_id)
        return PreviewPanelStateResponse()

    return router
