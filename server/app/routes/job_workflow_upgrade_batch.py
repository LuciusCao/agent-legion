from fastapi import APIRouter, Depends, HTTPException

from server.app.auth.dependencies import reject_studio_agent_scope
from server.app.routes.job_http import require_workflows_enabled
from server.app.routes.job_operation_contracts import (
    BatchJobMutationResponse,
    BatchUpgradeWorkflowRequest,
    JobMutationResultResponse,
)
from server.app.services import job_workflow_upgrade_batch
from server.app.services.job_selection_resolver import EmptyJobSelectionError
from server.app.services.job_workflow_upgrade import JobWorkflowUpgradeService
from server.app.settings import Settings


def create_job_workflow_upgrade_batch_router(
    job_workflow_upgrade: JobWorkflowUpgradeService,
    settings: Settings,
) -> APIRouter:
    router = APIRouter()

    @router.post(
        "/workspaces/{workspace_id}/jobs/batch-upgrade-workflow",
        response_model=BatchJobMutationResponse,
        dependencies=[Depends(reject_studio_agent_scope)],
    )
    def batch_upgrade_jobs_workflow(
        workspace_id: str, request: BatchUpgradeWorkflowRequest
    ) -> BatchJobMutationResponse:
        require_workflows_enabled(settings)
        try:
            results = job_workflow_upgrade_batch.batch_upgrade(
                job_workflow_upgrade,
                workspace_id,
                request.job_ids,
                job_filter=request.resolved_filter(),
                exclude_ids=request.exclude_ids,
            )
        except EmptyJobSelectionError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None
        return BatchJobMutationResponse(
            results=[JobMutationResultResponse.model_validate(result) for result in results]
        )

    return router
