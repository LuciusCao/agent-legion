"""Studio node prompt preview route (human-facing).

Mounted under the studio_secured() surface via create_workflow_revisions_router
(require_workspace_access + require_studio_authoring), alongside
workflow-drafts/validate|compare|publish. Read-only preview: it persists
nothing, so no reject_studio_agent_scope guard (same convention as the
draft-store GET — a scoped token may read what the initiating user may read).
"""

from fastapi import APIRouter

from server.app.jobs import JobQueries
from server.app.routes.job_http import raise_job_http_error, require_workflows_enabled
from server.app.routes.workflow_node_prompt_contracts import (
    NodePromptPreviewRequest,
    NodePromptPreviewResponse,
)
from server.app.services.job_errors import JobServiceError
from server.app.services.node_prompt_preview import preview_node_prompt
from server.app.settings import Settings


def create_workflow_node_prompt_router(job_db: JobQueries, settings: Settings) -> APIRouter:
    router = APIRouter()

    @router.post(
        "/workspaces/{workspace_id}/workflow/node-prompt-preview",
        response_model=NodePromptPreviewResponse,
    )
    def preview_node_prompt_route(
        workspace_id: str, request: NodePromptPreviewRequest
    ) -> NodePromptPreviewResponse:
        require_workflows_enabled(settings)
        try:
            payload = preview_node_prompt(
                job_db, workspace_id, request.node_key, request.definition_yaml
            )
        except JobServiceError as exc:
            raise_job_http_error(exc)
        return NodePromptPreviewResponse(**payload)

    return router
