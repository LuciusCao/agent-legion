from fastapi import APIRouter
from pydantic import BaseModel

from server.app.jobs import JobQueries
from server.app.routes.job_http import require_workflows_enabled
from server.app.services.workflow_drafts import (
    validate_workflow_definition,
    validate_workflow_for_publish,
    workflow_definition_from_yaml_string,
)
from server.app.services.workflow_revisions import WorkflowRevisionService
from server.app.settings import Settings


class WorkflowRevisionSummary(BaseModel):
    id: str
    workspace_id: str
    workflow_key: str
    version: int
    status: str
    definition_hash: str
    created_at: str
    published_at: str | None = None


class WorkflowRevisionsResponse(BaseModel):
    revisions: list[WorkflowRevisionSummary]


class WorkflowDraftRequest(BaseModel):
    definition_yaml: str


class WorkflowDraftValidationResponse(BaseModel):
    valid: bool
    errors: list[str]


def create_workflow_revisions_router(job_db: JobQueries, settings: Settings) -> APIRouter:
    router = APIRouter()

    @router.get(
        "/workspaces/{workspace_id}/workflow-revisions",
        response_model=WorkflowRevisionsResponse,
    )
    def list_workflow_revisions(workspace_id: str) -> WorkflowRevisionsResponse:
        require_workflows_enabled(settings)
        workspace = job_db.get_workspace(workspace_id)
        if workspace is None:
            return WorkflowRevisionsResponse(revisions=[])
        workflow_key = str(workspace.get("default_workflow_key") or "")
        rows = job_db.list_workflow_revisions(workspace_id, workflow_key)
        return WorkflowRevisionsResponse(revisions=[WorkflowRevisionSummary(**row) for row in rows])

    @router.post(
        "/workspaces/{workspace_id}/workflow-drafts/validate",
        response_model=WorkflowDraftValidationResponse,
    )
    def validate_workflow_draft(
        workspace_id: str,
        request: WorkflowDraftRequest,
    ) -> WorkflowDraftValidationResponse:
        require_workflows_enabled(settings)
        errors = validate_workflow_definition(request.definition_yaml)
        return WorkflowDraftValidationResponse(valid=not errors, errors=errors)

    @router.post(
        "/workspaces/{workspace_id}/workflow-drafts/publish",
        response_model=WorkflowDraftValidationResponse,
    )
    def publish_workflow_draft(
        workspace_id: str,
        request: WorkflowDraftRequest,
    ) -> WorkflowDraftValidationResponse:
        require_workflows_enabled(settings)
        structural_errors = validate_workflow_definition(request.definition_yaml)
        if structural_errors:
            return WorkflowDraftValidationResponse(valid=False, errors=structural_errors)
        definition = workflow_definition_from_yaml_string(request.definition_yaml)
        publish_errors = validate_workflow_for_publish(
            definition=definition,
            workspace_id=workspace_id,
            job_db=job_db,
            settings_executor_definitions=settings.executor_definitions,
        )
        if publish_errors:
            return WorkflowDraftValidationResponse(valid=False, errors=publish_errors)
        WorkflowRevisionService(job_db).publish_workspace_revision(workspace_id, definition)
        return WorkflowDraftValidationResponse(valid=True, errors=[])

    return router
