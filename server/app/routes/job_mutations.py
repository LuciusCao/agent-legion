"""Effecting job mutations: delete, rerun, run-to, continue and batch intake.

Every endpoint here takes effect immediately, so the whole router refuses
studio-agent scoped tokens (STUDIO-AGENT-001); job reads live in jobs.py.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from server.app.auth.dependencies import reject_studio_agent_scope
from server.app.routes.job_contracts import (
    DeleteJobResponse,
)
from server.app.routes.job_http import (
    raise_job_operation_error,
    require_workflows_enabled,
)
from server.app.routes.job_operation_contracts import (
    BatchJobIdsRequest,
    BatchJobMutationResponse,
    BatchRunToRequest,
    ContinueJobRequest,
    JobBatchRerunRequest,
    JobMutationResultResponse,
    RunToRequest,
)
from server.app.services.job_deletion import JobDeletionService
from server.app.services.job_execution import JobExecutionService
from server.app.services.job_operation_error import JobOperationError
from server.app.services.job_queries import JobQueryService
from server.app.services.job_rerun import JobRerunService
from server.app.settings import Settings


def create_job_mutations_router(
    job_queries: JobQueryService,
    job_rerun: JobRerunService,
    job_deletion: JobDeletionService,
    job_execution: JobExecutionService,
    settings: Settings,
) -> APIRouter:
    router = APIRouter(dependencies=[Depends(reject_studio_agent_scope)])

    @router.post(
        "/workspaces/{workspace_id}/jobs/batch-rerun",
        response_model=BatchJobMutationResponse,
    )
    def batch_rerun_workspace_jobs(
        workspace_id: str,
        payload: JobBatchRerunRequest,
    ) -> BatchJobMutationResponse:
        require_workflows_enabled(settings)
        results = job_rerun.batch_rerun(
            workspace_id,
            payload.job_ids,
            payload.node_key,
            from_failed_node=payload.from_failed_node,
            job_filter=payload.resolved_filter(),
            exclude_ids=payload.exclude_ids,
        )
        return BatchJobMutationResponse(
            results=[JobMutationResultResponse.model_validate(result) for result in results]
        )

    @router.delete("/workspaces/{workspace_id}/jobs/batch", response_model=BatchJobMutationResponse)
    def batch_delete_workspace_jobs(
        workspace_id: str,
        payload: BatchJobIdsRequest,
    ) -> BatchJobMutationResponse:
        require_workflows_enabled(settings)
        results = job_deletion.batch_delete(
            workspace_id,
            payload.job_ids,
            job_filter=payload.resolved_filter(),
            exclude_ids=payload.exclude_ids,
        )
        return BatchJobMutationResponse(
            results=[JobMutationResultResponse.model_validate(result) for result in results]
        )

    @router.post("/jobs/{job_id}/nodes/{node_key}/rerun", response_model=JobMutationResultResponse)
    def rerun_node(job_id: str, node_key: str) -> JobMutationResultResponse:
        require_workflows_enabled(settings)
        job = job_queries.job_db.get_job(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Job not found")
        try:
            result = job_rerun.rerun(job["workspace_id"], job_id, node_key)
        except JobOperationError as exc:
            raise_job_operation_error(exc)
        return JobMutationResultResponse.model_validate(result)

    @router.delete("/jobs/{job_id}", response_model=DeleteJobResponse)
    def delete_job(job_id: str) -> DeleteJobResponse:
        require_workflows_enabled(settings)
        job = job_queries.job_db.get_job(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Job not found")
        try:
            job_deletion.delete(job["workspace_id"], job_id)
        except JobOperationError as exc:
            raise_job_operation_error(exc)
        return DeleteJobResponse(deleted=job_id)

    @router.post("/jobs/{job_id}/run-to", response_model=JobMutationResultResponse)
    def run_to(job_id: str, payload: RunToRequest) -> JobMutationResultResponse:
        require_workflows_enabled(settings)
        job = job_queries.job_db.get_job(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Job not found")
        try:
            result = job_execution.run_to(
                job["workspace_id"],
                job_id,
                payload.target_node_key,
                payload.start_node_key,
            )
        except JobOperationError as exc:
            raise_job_operation_error(exc)
        return JobMutationResultResponse.model_validate(result)

    @router.post(
        "/jobs/{job_id}/continue",
        response_model=JobMutationResultResponse,
    )
    def continue_job(
        job_id: str,
        payload: ContinueJobRequest,
    ) -> JobMutationResultResponse:
        require_workflows_enabled(settings)
        job = job_queries.job_db.get_job(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Job not found")
        try:
            result = job_execution.continue_job(job["workspace_id"], job_id)
        except JobOperationError as exc:
            raise_job_operation_error(exc)
        return JobMutationResultResponse.model_validate(result)

    @router.post(
        "/workspaces/{workspace_id}/jobs/batch-run-to",
        response_model=BatchJobMutationResponse,
    )
    def batch_run_to(
        workspace_id: str,
        payload: BatchRunToRequest,
    ) -> BatchJobMutationResponse:
        require_workflows_enabled(settings)
        results = job_execution.batch_run_to(
            workspace_id,
            payload.job_ids,
            payload.target_node_key,
            payload.start_node_key,
            job_filter=payload.resolved_filter(),
            exclude_ids=payload.exclude_ids,
        )
        return BatchJobMutationResponse(
            results=[JobMutationResultResponse.model_validate(result) for result in results]
        )

    return router
