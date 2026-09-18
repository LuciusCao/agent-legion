"""Batch rerun preview route: read-only eligible/total counts."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from server.app.auth.dependencies import reject_studio_agent_scope
from server.app.routes.job_rerun_preview_contracts import (
    BatchRerunPreviewResponse,
    JobBatchRerunPreviewRequest,
)
from server.app.services.job_rerun import JobRerunService
from server.app.services.job_rerun.preview import batch_rerun_preview


def create_batch_rerun_preview_router(
    job_rerun: JobRerunService,
) -> APIRouter:
    router = APIRouter()

    @router.post(
        "/workspaces/{workspace_id}/jobs/batch-rerun/preview",
        response_model=BatchRerunPreviewResponse,
        # The job guard's scoped effecting short-circuit assumes every POST
        # under job_group refuses scoped tokens (red-team R8 P2-2 on #745):
        # without this dependency a scoped token skips the workspace
        # membership check this route otherwise relies on.
        dependencies=[Depends(reject_studio_agent_scope)],
    )
    def preview_batch_rerun_workspace_jobs(
        workspace_id: str,
        payload: JobBatchRerunPreviewRequest,
    ) -> BatchRerunPreviewResponse:
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
