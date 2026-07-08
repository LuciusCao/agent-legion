from __future__ import annotations

from fastapi import APIRouter

from server.app.routes.job_http import raise_job_http_error, require_workflows_enabled
from server.app.routes.job_view_contracts import JobsSnapshotResponse
from server.app.services.job_errors import JobServiceError
from server.app.services.job_patch_queries import JobPatchQueryService
from server.app.settings import Settings


def create_job_snapshot_router(
    job_patch_queries: JobPatchQueryService,
    settings: Settings,
) -> APIRouter:
    router = APIRouter()

    @router.get("/workspaces/{workspace_id}/jobs/snapshot", response_model=JobsSnapshotResponse)
    def snapshot_workspace_jobs(
        workspace_id: str,
        limit: int = 200,
        cursor: str | None = None,
    ) -> JobsSnapshotResponse:
        require_workflows_enabled(settings)
        try:
            safe_limit = max(1, min(limit, 500))
            return JobsSnapshotResponse(
                **job_patch_queries.snapshot(workspace_id, limit=safe_limit, cursor=cursor)
            )
        except JobServiceError as exc:
            raise_job_http_error(exc)

    return router
