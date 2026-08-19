"""Batch job pause/resume endpoints (execution_paused flag).

Effecting mutations like the sibling job_mutations router: the whole router
refuses studio-agent scoped tokens (STUDIO-AGENT-001).
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends

from server.app.auth.dependencies import reject_studio_agent_scope, require_user
from server.app.routes.job_http import require_workflows_enabled
from server.app.routes.job_operation_contracts import (
    BatchJobMutationResponse,
    BatchPauseJobsRequest,
    BatchResumeJobsRequest,
    JobMutationResultResponse,
)
from server.app.services.job_pause import JobPauseService
from server.app.settings import Settings


def create_job_pause_router(
    job_pause: JobPauseService,
    settings: Settings,
) -> APIRouter:
    router = APIRouter(dependencies=[Depends(reject_studio_agent_scope)])

    @router.post(
        "/workspaces/{workspace_id}/jobs/batch-pause",
        response_model=BatchJobMutationResponse,
    )
    def batch_pause_workspace_jobs(
        workspace_id: str,
        payload: BatchPauseJobsRequest,
        user: Annotated[dict[str, Any], Depends(require_user)],
    ) -> BatchJobMutationResponse:
        require_workflows_enabled(settings)
        results = job_pause.batch_pause(
            workspace_id,
            payload.job_ids,
            payload.reason,
            operator=f"user:{user['id']}",
            job_filter=payload.resolved_filter(),
            exclude_ids=payload.exclude_ids,
        )
        return BatchJobMutationResponse(
            results=[JobMutationResultResponse.model_validate(result) for result in results]
        )

    @router.post(
        "/workspaces/{workspace_id}/jobs/batch-resume",
        response_model=BatchJobMutationResponse,
    )
    def batch_resume_workspace_jobs(
        workspace_id: str,
        payload: BatchResumeJobsRequest,
    ) -> BatchJobMutationResponse:
        require_workflows_enabled(settings)
        results = job_pause.batch_resume(
            workspace_id,
            payload.job_ids,
            job_filter=payload.resolved_filter(),
            exclude_ids=payload.exclude_ids,
        )
        return BatchJobMutationResponse(
            results=[JobMutationResultResponse.model_validate(result) for result in results]
        )

    return router
