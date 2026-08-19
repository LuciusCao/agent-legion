from fastapi import APIRouter, Depends, Request

from server.app.auth.dependencies import reject_studio_agent_scope
from server.app.jobs import JobQueries
from server.app.routes.job_http import raise_job_http_error, require_workflows_enabled
from server.app.routes.workflow_revisions_contracts import (
    WorkflowDraftRequest,
    WorkflowDraftValidationResponse,
)
from server.app.scheduler_wakeup import notify_schedulable_work, reload_worker_scan_entries
from server.app.services.job_errors import JobServiceError
from server.app.services.workflow_draft_key import require_draft_workflow_key_match
from server.app.services.workflow_draft_publish import (
    publish_workflow_draft,
    validate_workflow_draft_for_publish,
)
from server.app.settings import Settings


def create_workflow_draft_publish_router(job_db: JobQueries, settings: Settings) -> APIRouter:
    router = APIRouter()

    @router.post(
        "/workspaces/{workspace_id}/workflow-drafts/validate",
        response_model=WorkflowDraftValidationResponse,
    )
    def validate_workflow_draft(
        workspace_id: str,
        request: WorkflowDraftRequest,
    ) -> WorkflowDraftValidationResponse:
        require_workflows_enabled(settings)
        # Same validation set as publish (structure + node code resolvability),
        # so config errors surface here instead of only at publish time.
        errors = validate_workflow_draft_for_publish(
            job_db,
            workspace_id,
            request.definition_yaml,
            settings.executor_runtime.workflows.custom_nodes_enabled,
        )
        return WorkflowDraftValidationResponse(valid=not errors, errors=errors)

    @router.post(
        "/workspaces/{workspace_id}/workflow-drafts/publish",
        response_model=WorkflowDraftValidationResponse,
        dependencies=[Depends(reject_studio_agent_scope)],
    )
    def publish_draft(
        workspace_id: str,
        request: WorkflowDraftRequest,
        http_request: Request,
    ) -> WorkflowDraftValidationResponse:
        require_workflows_enabled(settings)
        try:
            require_draft_workflow_key_match(job_db, workspace_id, request.definition_yaml)
        except JobServiceError as exc:
            raise_job_http_error(exc)
        valid, errors = publish_workflow_draft(
            job_db,
            workspace_id,
            request.definition_yaml,
            settings.executor_runtime.workflows.custom_nodes_enabled,
        )
        if valid:
            # The first publish of a blank-canvas workspace adopts the draft
            # key, making the workspace a new worker scan target (schema
            # v50): reload the scan list and wake the poll loop.
            reload_worker_scan_entries(http_request)
            notify_schedulable_work()
        return WorkflowDraftValidationResponse(valid=valid, errors=errors)

    return router
