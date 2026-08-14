from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from server.app.auth.dependencies import reject_studio_agent_scope
from server.app.routes.job_http import require_workflows_enabled
from server.app.routes.job_operation_contracts import JobMutationResultResponse
from server.app.services.job_queries import JobQueryService
from server.app.services.job_workflow_upgrade import JobWorkflowUpgradeService
from server.app.settings import Settings


def create_job_workflow_upgrade_router(
    job_queries: JobQueryService,
    job_workflow_upgrade: JobWorkflowUpgradeService,
    settings: Settings,
) -> APIRouter:
    router = APIRouter()

    @router.post(
        "/jobs/{job_id}/upgrade-workflow",
        response_model=JobMutationResultResponse,
        dependencies=[Depends(reject_studio_agent_scope)],
    )
    def upgrade_job_workflow(job_id: str) -> JobMutationResultResponse:
        require_workflows_enabled(settings)
        job = job_queries.job_db.get_job(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Job not found")
        result = job_workflow_upgrade.upgrade(job["workspace_id"], job_id)
        if result["status"] != "succeeded":
            status_code = 404 if result.get("reason_code") == "not_found" else 400
            raise HTTPException(
                status_code=status_code,
                detail=result.get("message") or result.get("reason_code") or "Upgrade failed",
            )
        return JobMutationResultResponse.model_validate(result)

    return router
