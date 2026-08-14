from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends

from server.app.auth.dependencies import reject_studio_agent_scope
from server.app.jobs import JobQueries
from server.app.routes.failed_node_run_contracts import (
    FailedNodeRunItem,
    FailedNodeRunsResponse,
)
from server.app.routes.job_http import raise_job_http_error, require_workflows_enabled
from server.app.routes.job_rerun_by_failure_contracts import (
    JobRerunByFailureRequest,
    JobRerunByFailureResponse,
    JobRerunByFailureResultResponse,
)
from server.app.services.failed_node_runs import FailedNodeRunQueryService
from server.app.services.job_errors import JobServiceError
from server.app.services.job_rerun import JobRerunService
from server.app.settings import Settings


def create_failed_node_runs_router(
    job_db: JobQueries,
    job_rerun: JobRerunService,
    settings: Settings,
) -> APIRouter:
    router = APIRouter()
    queries = FailedNodeRunQueryService(job_db)

    @router.get(
        "/workspaces/{workspace_id}/failed-node-runs",
        response_model=FailedNodeRunsResponse,
    )
    def list_failed_node_runs(
        workspace_id: str,
        category: str | None = None,
        detail: str | None = None,
        workflow_key: str | None = None,
        since: datetime | None = None,
    ) -> FailedNodeRunsResponse:
        require_workflows_enabled(settings)
        try:
            rows = queries.list_failed_node_runs(
                workspace_id,
                category=category,
                detail=detail,
                workflow_key=workflow_key,
                since=since,
            )
            return FailedNodeRunsResponse(runs=[FailedNodeRunItem(**row) for row in rows])
        except JobServiceError as exc:
            raise_job_http_error(exc)

    @router.post(
        "/workspaces/{workspace_id}/jobs/rerun-by-failure",
        response_model=JobRerunByFailureResponse,
        dependencies=[Depends(reject_studio_agent_scope)],
    )
    def rerun_jobs_by_failure_category(
        workspace_id: str,
        payload: JobRerunByFailureRequest,
    ) -> JobRerunByFailureResponse:
        require_workflows_enabled(settings)
        results = job_rerun.rerun_by_failure_category(
            workspace_id,
            payload.category,
            strategy=payload.strategy,
            job_ids=payload.job_ids,
            workflow_key=payload.workflow_key,
            job_filter=payload.filter.to_filter() if payload.filter is not None else None,
            exclude_ids=payload.exclude_ids,
            from_node_key=payload.from_node_key,
        )
        return JobRerunByFailureResponse(
            results=[JobRerunByFailureResultResponse.model_validate(result) for result in results]
        )

    return router
