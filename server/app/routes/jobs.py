from __future__ import annotations

from fastapi import APIRouter

from server.app.routes.job_contracts import (
    BatchJobRequest,
    BatchJobResponse,
    DeleteJobResponse,
    JobDetailResponse,
    JobsResponse,
    RerunNodeResponse,
)
from server.app.routes.job_http import raise_job_http_error, require_pipelines_enabled
from server.app.services.job_errors import JobServiceError
from server.app.services.job_queries import JobQueryService
from server.app.services.job_rerun import JobRerunService
from server.app.settings import Settings


def create_jobs_router(
    job_queries: JobQueryService,
    job_rerun: JobRerunService,
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
                jobs=job_queries.list_jobs(workspace_id, pipeline_key=pipeline_key, status=status)
            )
        except JobServiceError as exc:
            raise_job_http_error(exc)

    @router.post("/workspaces/{workspace_id}/jobs/batch-rerun", response_model=BatchJobResponse)
    def batch_rerun_workspace_jobs(
        workspace_id: str,
        payload: BatchJobRequest,
    ) -> BatchJobResponse:
        require_pipelines_enabled(settings)
        try:
            return BatchJobResponse(results=job_rerun.batch_rerun(workspace_id, payload.job_ids))
        except JobServiceError as exc:
            raise_job_http_error(exc)

    @router.delete("/workspaces/{workspace_id}/jobs/batch", response_model=BatchJobResponse)
    def batch_delete_workspace_jobs(
        workspace_id: str,
        payload: BatchJobRequest,
    ) -> BatchJobResponse:
        require_pipelines_enabled(settings)
        try:
            return BatchJobResponse(results=job_rerun.batch_delete(workspace_id, payload.job_ids))
        except JobServiceError as exc:
            raise_job_http_error(exc)

    @router.get("/jobs", response_model=JobsResponse)
    def list_jobs(pipeline_key: str | None = None, status: str | None = None) -> JobsResponse:
        require_pipelines_enabled(settings)
        try:
            return JobsResponse(
                jobs=job_queries.list_jobs("default", pipeline_key=pipeline_key, status=status)
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

    @router.post("/jobs/{job_id}/nodes/{node_key}/rerun", response_model=RerunNodeResponse)
    def rerun_node(job_id: str, node_key: str) -> RerunNodeResponse:
        require_pipelines_enabled(settings)
        try:
            return RerunNodeResponse(**job_rerun.rerun_node(job_id, node_key))
        except JobServiceError as exc:
            raise_job_http_error(exc)

    @router.delete("/jobs/{job_id}", response_model=DeleteJobResponse)
    def delete_job(job_id: str) -> DeleteJobResponse:
        require_pipelines_enabled(settings)
        try:
            return DeleteJobResponse(deleted=job_rerun.delete(job_id))
        except JobServiceError as exc:
            raise_job_http_error(exc)

    return router
