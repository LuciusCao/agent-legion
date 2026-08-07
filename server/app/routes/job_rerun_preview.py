"""Batch rerun preview route: read-only eligible/total counts."""

from __future__ import annotations

from fastapi import APIRouter

from server.app.routes.job_http import require_workflows_enabled
from server.app.routes.job_rerun_preview_contracts import (
    BatchRerunPreviewResponse,
    JobBatchRerunPreviewRequest,
)
from server.app.services._job_rerun_preview import batch_rerun_preview
from server.app.services.job_rerun import JobRerunService
from server.app.settings import Settings


def create_batch_rerun_preview_router(
    job_rerun: JobRerunService,
    settings: Settings,
) -> APIRouter:
    router = APIRouter()

    @router.post(
        "/workspaces/{workspace_id}/jobs/batch-rerun/preview",
        response_model=BatchRerunPreviewResponse,
    )
    def preview_batch_rerun_workspace_jobs(
        workspace_id: str,
        payload: JobBatchRerunPreviewRequest,
    ) -> BatchRerunPreviewResponse:
        require_workflows_enabled(settings)
        counts = batch_rerun_preview(
            job_rerun,
            workspace_id,
            payload.job_ids,
            payload.node_key,
            from_failed_node=payload.from_failed_node,
            failure_category=payload.failure_category,
            job_filter=payload.resolved_filter(),
            exclude_ids=payload.exclude_ids,
        )
        return BatchRerunPreviewResponse(**counts)

    return router
