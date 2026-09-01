"""Studio-agent preview panel tool endpoints (issue #328).

Read (``GET .../preview/context``, ``GET .../preview/panel``) and draft write
(``PUT .../preview/panel/draft``) for the workspace preview panel bundle. All
draft-only by design: publishing stays a human action on the secured route
surface (``preview_panels.py``, STUDIO-AGENT-001). The endpoints are
workspace-bound, so they mount on the workspace_scoped router inside
``studio_agent_tools.create_studio_agent_tools_router`` (scoped token +
workspace binding), split out here for the file-size budget.
"""

from typing import Annotated, Any

from fastapi import APIRouter, Depends

from server.app.auth.dependencies import require_studio_agent_scope
from server.app.jobs import JobQueries
from server.app.routes.job_http import raise_job_http_error
from server.app.routes.studio_agent_preview_contracts import (
    PreviewContextResponse,
    PreviewPanelDraftRequest,
    PreviewPanelStateResponse,
    PreviewPanelVersionResponse,
)
from server.app.services.job_errors import JobServiceError, NotFoundError
from server.app.services.preview_panels import PreviewPanelService, get_preview_context
from server.app.services.studio_agent_tools import studio_agent_created_by
from server.app.settings import Settings


def create_studio_agent_preview_tools_router(job_db: JobQueries, settings: Settings) -> APIRouter:
    router = APIRouter()

    @router.get(
        "/studio-agent/tools/workspaces/{workspace_id}/preview/context",
        response_model=PreviewContextResponse,
    )
    def preview_context(workspace_id: str, job_id: str | None = None) -> PreviewContextResponse:
        try:
            context = get_preview_context(job_db, settings, workspace_id, job_id)
        except JobServiceError as exc:
            raise_job_http_error(exc)
        return PreviewContextResponse.model_validate(context)

    @router.get(
        "/studio-agent/tools/workspaces/{workspace_id}/preview/panel",
        response_model=PreviewPanelStateResponse,
    )
    def get_preview_panel(workspace_id: str) -> PreviewPanelStateResponse:
        if job_db.get_workspace(workspace_id) is None:
            raise_job_http_error(NotFoundError("Workspace not found"))
        return PreviewPanelStateResponse.model_validate(
            PreviewPanelService(job_db).get_state(workspace_id)
        )

    @router.put(
        "/studio-agent/tools/workspaces/{workspace_id}/preview/panel/draft",
        response_model=PreviewPanelVersionResponse,
    )
    def save_preview_panel_draft(
        workspace_id: str,
        payload: PreviewPanelDraftRequest,
        user: Annotated[dict[str, Any], Depends(require_studio_agent_scope)],
    ) -> PreviewPanelVersionResponse:
        if job_db.get_workspace(workspace_id) is None:
            raise_job_http_error(NotFoundError("Workspace not found"))
        try:
            row = PreviewPanelService(job_db).save_draft(
                workspace_id,
                payload.html,
                studio_agent_created_by(str(user["id"])),
                payload.change_note,
            )
        except JobServiceError as exc:
            raise_job_http_error(exc)
        return PreviewPanelVersionResponse.model_validate(row)

    return router
