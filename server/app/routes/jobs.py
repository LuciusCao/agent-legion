from __future__ import annotations

from typing import Any, cast

from fastapi import APIRouter, HTTPException

from server.app.routes.job_contracts import (
    DeleteJobResponse,
)
from server.app.routes.job_http import raise_job_http_error, require_workflows_enabled
from server.app.routes.job_operation_contracts import (
    BatchJobIdsRequest,
    BatchJobMutationResponse,
    BatchRunToRequest,
    ContinueJobRequest,
    JobBatchRerunRequest,
    JobMutationResultResponse,
    RunToRequest,
)
from server.app.routes.job_view_contracts import (
    JobDetailResponse,
    JobsResponse,
    JobSummaryResponse,
)
from server.app.services.job_deletion import JobDeletionService
from server.app.services.job_errors import JobServiceError
from server.app.services.job_execution import JobExecutionService
from server.app.services.job_queries import JobQueryService
from server.app.services.job_rerun import JobRerunService
from server.app.settings import Settings


def create_jobs_router(
    job_queries: JobQueryService,
    job_rerun: JobRerunService,
    job_deletion: JobDeletionService,
    job_execution: JobExecutionService,
    settings: Settings,
) -> APIRouter:
    router = APIRouter()

    @router.get("/workspaces/{workspace_id}/jobs", response_model=JobsResponse)
    def list_workspace_jobs(
        workspace_id: str,
        workflow_key: str | None = None,
        status: str | None = None,
    ) -> JobsResponse:
        require_workflows_enabled(settings)
        try:
            return JobsResponse(
                jobs=cast(
                    list[JobSummaryResponse],
                    job_queries.list_jobs(workspace_id, workflow_key=workflow_key, status=status),
                )
            )
        except JobServiceError as exc:
            raise_job_http_error(exc)

    @router.post(
        "/workspaces/{workspace_id}/jobs/batch-rerun",
        response_model=BatchJobMutationResponse,
    )
    def batch_rerun_workspace_jobs(
        workspace_id: str,
        payload: JobBatchRerunRequest,
    ) -> BatchJobMutationResponse:
        require_workflows_enabled(settings)
        results = job_rerun.batch_rerun(workspace_id, payload.job_ids, payload.node_key)
        return BatchJobMutationResponse(
            results=[JobMutationResultResponse.model_validate(result) for result in results]
        )

    @router.delete("/workspaces/{workspace_id}/jobs/batch", response_model=BatchJobMutationResponse)
    def batch_delete_workspace_jobs(
        workspace_id: str,
        payload: BatchJobIdsRequest,
    ) -> BatchJobMutationResponse:
        require_workflows_enabled(settings)
        results = job_deletion.batch_delete(workspace_id, payload.job_ids)
        return BatchJobMutationResponse(
            results=[JobMutationResultResponse.model_validate(result) for result in results]
        )

    @router.get("/jobs", response_model=JobsResponse)
    def list_jobs(workflow_key: str | None = None, status: str | None = None) -> JobsResponse:
        require_workflows_enabled(settings)
        try:
            return JobsResponse(
                jobs=cast(
                    list[JobSummaryResponse],
                    job_queries.list_jobs("default", workflow_key=workflow_key, status=status),
                )
            )
        except JobServiceError as exc:
            raise_job_http_error(exc)

    @router.get("/jobs/{job_id}", response_model=JobDetailResponse)
    def get_job(job_id: str) -> JobDetailResponse:
        require_workflows_enabled(settings)
        try:
            return JobDetailResponse(**job_queries.detail(job_id))
        except JobServiceError as exc:
            raise_job_http_error(exc)

    @router.post("/jobs/{job_id}/nodes/{node_key}/rerun", response_model=JobMutationResultResponse)
    def rerun_node(job_id: str, node_key: str) -> JobMutationResultResponse:
        require_workflows_enabled(settings)
        job = job_queries.job_db.get_job(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Job not found")
        result = job_rerun.rerun(job["workspace_id"], job_id, node_key)
        if result["status"] != "succeeded":
            status_code = 400
            reason_code = result.get("reason_code")
            if reason_code in ("not_found", "node_not_found"):
                status_code = 404
            raise HTTPException(
                status_code=status_code,
                detail=result.get("message") or reason_code or "Rerun failed",
            )
        return JobMutationResultResponse.model_validate(result)

    @router.delete("/jobs/{job_id}", response_model=DeleteJobResponse)
    def delete_job(job_id: str) -> DeleteJobResponse:
        require_workflows_enabled(settings)
        job = job_queries.job_db.get_job(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Job not found")
        result = job_deletion.delete(job["workspace_id"], job_id)
        if result["status"] != "succeeded":
            status_code = 400
            if result.get("reason_code") == "not_found":
                status_code = 404
            raise HTTPException(
                status_code=status_code,
                detail=result.get("message") or result.get("reason_code") or "Delete failed",
            )
        return DeleteJobResponse(deleted=job_id)

    def _raise_for_run_to_result(result: dict[str, Any]) -> None:
        if result["status"] == "succeeded":
            return
        status_code = 400
        reason_code = result.get("reason_code")
        if reason_code in ("not_found", "node_not_found"):
            status_code = 404
        raise HTTPException(
            status_code=status_code,
            detail=result.get("message") or reason_code or "Run-to failed",
        )

    @router.post("/jobs/{job_id}/run-to", response_model=JobMutationResultResponse)
    def run_to(job_id: str, payload: RunToRequest) -> JobMutationResultResponse:
        require_workflows_enabled(settings)
        job = job_queries.job_db.get_job(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Job not found")
        result = job_execution.run_to(
            job["workspace_id"],
            job_id,
            payload.target_node_key,
            payload.start_node_key,
        )
        _raise_for_run_to_result(result)
        return JobMutationResultResponse.model_validate(result)

    @router.post("/jobs/{job_id}/continue", response_model=JobMutationResultResponse)
    def continue_job(
        job_id: str,
        payload: ContinueJobRequest,
    ) -> JobMutationResultResponse:
        require_workflows_enabled(settings)
        job = job_queries.job_db.get_job(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Job not found")
        result = job_execution.continue_job(job["workspace_id"], job_id)
        if result["status"] != "succeeded":
            status_code = 400
            if result.get("reason_code") == "not_found":
                status_code = 404
            raise HTTPException(
                status_code=status_code,
                detail=result.get("message") or result.get("reason_code") or "Continue failed",
            )
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
        )
        return BatchJobMutationResponse(
            results=[JobMutationResultResponse.model_validate(result) for result in results]
        )

    return router
