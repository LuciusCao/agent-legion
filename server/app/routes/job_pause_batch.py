"""Batch job pause/resume endpoints (execution_paused flag).

Effecting mutations like the sibling job_mutations router: the whole router
refuses studio-agent scoped tokens (STUDIO-AGENT-001). Selections above the
sync threshold enqueue a background operation and return 202 (#946).
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from server.app.auth.dependencies import reject_studio_agent_scope, require_user
from server.app.routes.job_batch_operations import ACCEPTED_202, async_dispatch_or_none
from server.app.routes.job_operation_contracts import (
    BatchJobMutationResponse,
    BatchPauseJobsRequest,
    BatchResumeJobsRequest,
    JobMutationResultResponse,
)
from server.app.services.job_pause import JobPauseService


def create_job_pause_router(
    job_pause: JobPauseService,
) -> APIRouter:
    router = APIRouter(dependencies=[Depends(reject_studio_agent_scope)])
    job_db = job_pause.job_db

    @router.post(
        "/workspaces/{workspace_id}/jobs/batch-pause",
        response_model=BatchJobMutationResponse,
        responses=ACCEPTED_202,
    )
    def batch_pause_workspace_jobs(
        workspace_id: str,
        payload: BatchPauseJobsRequest,
        user: Annotated[dict[str, Any], Depends(require_user)],
    ) -> BatchJobMutationResponse | JSONResponse:
        operator = f"user:{user['id']}"
        # #946: selections above the sync threshold enqueue (202 + handle).
        ids, accepted = async_dispatch_or_none(
            job_db,
            workspace_id,
            "pause",
            payload,
            {"reason": payload.reason, "operator": operator},
            user,
        )
        if accepted is not None:
            return accepted
        results = job_pause.batch_pause(workspace_id, ids, payload.reason, operator=operator)
        return BatchJobMutationResponse(
            results=[JobMutationResultResponse.model_validate(result) for result in results]
        )

    @router.post(
        "/workspaces/{workspace_id}/jobs/batch-resume",
        response_model=BatchJobMutationResponse,
        responses=ACCEPTED_202,
    )
    def batch_resume_workspace_jobs(
        workspace_id: str,
        payload: BatchResumeJobsRequest,
        user: Annotated[dict[str, Any], Depends(require_user)],
    ) -> BatchJobMutationResponse | JSONResponse:
        ids, accepted = async_dispatch_or_none(job_db, workspace_id, "resume", payload, {}, user)
        if accepted is not None:
            return accepted
        results = job_pause.batch_resume(workspace_id, ids)
        return BatchJobMutationResponse(
            results=[JobMutationResultResponse.model_validate(result) for result in results]
        )

    return router
