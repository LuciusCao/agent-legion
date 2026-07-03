import json

from fastapi import APIRouter, HTTPException

import server.app.routes.workflow_contracts as workflow_contracts
from server.app.jobs import JobQueries
from server.app.routes.job_http import require_workflows_enabled
from server.app.routes.workflow_draft_compare import create_workflow_draft_compare_router
from server.app.routes.workflow_revisions_contracts import (
    ActiveWorkflowRevisionResponse,
    WorkflowDraftRequest,
    WorkflowDraftValidationResponse,
    WorkflowRevisionsResponse,
    WorkflowRevisionSummary,
)
from server.app.services.workflow_draft_publish import publish_workflow_draft
from server.app.services.workflow_drafts import validate_workflow_definition
from server.app.services.workflow_revision_format import (
    definition_to_yaml,
    workflow_definition_to_response_payload,
)
from server.app.settings import Settings
from server.app.workflows.definition import workflow_definition_from_dict


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

    @router.get(
        "/workspaces/{workspace_id}/workflow-revisions/active",
        response_model=ActiveWorkflowRevisionResponse,
    )
    def get_active_workflow_revision(workspace_id: str) -> ActiveWorkflowRevisionResponse:
        require_workflows_enabled(settings)
        workspace = job_db.get_workspace(workspace_id)
        if workspace is None:
            raise HTTPException(status_code=404, detail="Workspace not found")
        workflow_key = str(workspace.get("default_workflow_key") or "")
        revision = job_db.get_active_workflow_revision(workspace_id, workflow_key)
        if revision is None:
            raise HTTPException(status_code=404, detail="No active workflow revision")
        definition = workflow_definition_from_dict(json.loads(str(revision["definition_json"])))
        return ActiveWorkflowRevisionResponse(
            revision=WorkflowRevisionSummary.model_validate(revision),
            workflow=workflow_contracts.WorkflowDefinitionResponse.model_validate(
                workflow_definition_to_response_payload(definition)
            ),
            definition_yaml=definition_to_yaml(definition),
        )

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
    def publish_draft(
        workspace_id: str,
        request: WorkflowDraftRequest,
    ) -> WorkflowDraftValidationResponse:
        require_workflows_enabled(settings)
        valid, errors = publish_workflow_draft(
            job_db,
            workspace_id,
            request.definition_yaml,
            settings.executor_definitions,
        )
        return WorkflowDraftValidationResponse(valid=valid, errors=errors)

    router.include_router(create_workflow_draft_compare_router(job_db, settings))
    return router
