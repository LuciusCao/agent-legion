from __future__ import annotations

from typing import cast

from fastapi import APIRouter, HTTPException

from server.app.routes.job_contracts import (
    DeleteJobResponse,
)
from server.app.routes.job_http import raise_job_http_error, require_pipelines_enabled
from server.app.routes.job_operation_contracts import (
    BatchJobIdsRequest,
    BatchJobMutationResponse,
    BatchRerunRequest,
    JobMutationResultResponse,
)
from server.app.routes.job_view_contracts import (
    JobDetailResponse,
    JobsResponse,
    JobSummaryResponse,
)
from server.app.services.job_deletion import JobDeletionService
from server.app.services.job_errors import JobServiceError
from server.app.services.job_queries import JobQueryService
from server.app.services.job_rerun import JobRerunService
from server.app.settings import Settings


def create_jobs_router(
    job_queries: JobQueryService,
    job_rerun: JobRerunService,
    job_deletion: JobDeletionService,
    settings: Settings,
) -> APIRouter:
    router = APIRouter()

    @router.get("/workspaces/{workspace_id}/jobs", response_model=JobsResponse)
    def list_workspace_jobs(
        workspace_id: str,
        pipeline_key: str | None = None,
        status: str | None = None,
    ) -> JobsResponse:
        require_pipelines_enabled(settings)
        try:
            return JobsResponse(
                jobs=cast(
                    list[JobSummaryResponse],
                    job_queries.list_jobs(workspace_id, pipeline_key=pipeline_key, status=status),
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
        payload: BatchRerunRequest,
    ) -> BatchJobMutationResponse:
        require_pipelines_enabled(settings)
        results = job_rerun.batch_rerun(workspace_id, payload.job_ids, payload.node_key)
        return BatchJobMutationResponse(
            results=[JobMutationResultResponse.model_validate(result) for result in results]
        )

    @router.delete("/workspaces/{workspace_id}/jobs/batch", response_model=BatchJobMutationResponse)
    def batch_delete_workspace_jobs(
        workspace_id: str,
        payload: BatchJobIdsRequest,
    ) -> BatchJobMutationResponse:
        require_pipelines_enabled(settings)
        results = job_deletion.batch_delete(workspace_id, payload.job_ids)
        return BatchJobMutationResponse(
            results=[JobMutationResultResponse.model_validate(result) for result in results]
        )

    @router.get("/jobs", response_model=JobsResponse)
    def list_jobs(pipeline_key: str | None = None, status: str | None = None) -> JobsResponse:
        require_pipelines_enabled(settings)
        try:
            return JobsResponse(
                jobs=cast(
                    list[JobSummaryResponse],
                    job_queries.list_jobs("default", pipeline_key=pipeline_key, status=status),
                )
            )
        except JobServiceError as exc:
            raise_job_http_error(exc)

    @router.get("/jobs/{job_id}", response_model=JobDetailResponse)
    def get_job(job_id: str) -> JobDetailResponse:
        require_pipelines_enabled(settings)
        try:
            return JobDetailResponse(**job_queries.detail(job_id))
        except JobServiceError as exc:
            raise_job_http_error(exc)

    @router.post("/jobs/{job_id}/nodes/{node_key}/rerun", response_model=JobMutationResultResponse)
    def rerun_node(job_id: str, node_key: str) -> JobMutationResultResponse:
        require_pipelines_enabled(settings)
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
        require_pipelines_enabled(settings)
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

    return router
