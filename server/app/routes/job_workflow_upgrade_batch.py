from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse

from server.app.auth.dependencies import reject_studio_agent_scope, require_user
from server.app.routes.job_batch_operations import ACCEPTED_202, async_dispatch_or_none
from server.app.routes.job_operation_contracts import (
    BatchJobMutationResponse,
    BatchUpgradeWorkflowRequest,
    JobMutationResultResponse,
)
from server.app.services import job_workflow_upgrade_batch
from server.app.services.job_selection_resolver import EmptyJobSelectionError
from server.app.services.job_workflow_upgrade import JobWorkflowUpgradeService


def create_job_workflow_upgrade_batch_router(
    job_workflow_upgrade: JobWorkflowUpgradeService,
) -> APIRouter:
    router = APIRouter()

    @router.post(
        "/workspaces/{workspace_id}/jobs/batch-upgrade-workflow",
        response_model=BatchJobMutationResponse,
        responses=ACCEPTED_202,
        dependencies=[Depends(reject_studio_agent_scope)],
    )
    def batch_upgrade_jobs_workflow(
        workspace_id: str,
        request: BatchUpgradeWorkflowRequest,
        user: Annotated[dict[str, Any], Depends(require_user)],
    ) -> BatchJobMutationResponse | JSONResponse:
        job_db = job_workflow_upgrade.job_db
        # #946: selections above the sync threshold enqueue (202 + handle);
        # the empty-selection 400 stays synchronous either way.
        ids, accepted = async_dispatch_or_none(
            job_db,
            workspace_id,
            "upgrade_workflow",
            payload=request,
            params={"mode": request.mode},
            user=user,
        )
        if accepted is not None:
            return accepted
        try:
            results = job_workflow_upgrade_batch.batch_upgrade(
                job_workflow_upgrade,
                workspace_id,
                ids,
                mode=request.mode,
            )
        except EmptyJobSelectionError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None
        return BatchJobMutationResponse(
            results=[JobMutationResultResponse.model_validate(result) for result in results]
        )

    return router
