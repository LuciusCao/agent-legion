from fastapi import APIRouter, HTTPException

from server.app.jobs import JobQueries
from server.app.routes.job_http import require_workflows_enabled
from server.app.routes.workflow_draft_compare_contracts import (
    WorkflowDraftCompareRequest,
    WorkflowDraftCompareResponse,
)
from server.app.services.workflow_draft_compare import compare_workflow_draft
from server.app.settings import Settings


def create_workflow_draft_compare_router(job_db: JobQueries, settings: Settings) -> APIRouter:
    router = APIRouter()

    @router.post(
        "/workspaces/{workspace_id}/workflow-drafts/compare",
        response_model=WorkflowDraftCompareResponse,
    )
    def compare_workflow_draft_route(
        workspace_id: str,
        request: WorkflowDraftCompareRequest,
    ) -> WorkflowDraftCompareResponse:
        require_workflows_enabled(settings)
        workspace = job_db.get_workspace(workspace_id)
        if workspace is None:
            raise HTTPException(status_code=404, detail="Workspace not found")
        result = compare_workflow_draft(
            job_db,
            workspace_id,
            request.definition_yaml,
            resource_providers=settings.resource_providers.providers,
        )
        return WorkflowDraftCompareResponse.model_validate(result)

    return router
