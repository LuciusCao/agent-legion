"""Batch-ops task-center list/archive routes (M61).

``GET /workspaces/{id}/job-batch-operations`` feeds the Studio task center's
unified batch-op cards (three views + badge count, same contract shape as
the #1157 run-batches surface); ``POST .../job-batch-operations/{op_id}/archive``
is the manual archive — idempotent by design (a repeat archive returns the
current state, not run_batches' 409).

Mounted from ``routes/job_batch_operations.py`` (the job route group applies
``require_workspace_access``, same as the #946 polling endpoints); the
single-op status/results routes stay in that module. The archive write
additionally mounts ``reject_studio_agent_scope`` (STUDIO-AGENT-001) like
every other effecting workspace write. Contracts live here
next to the router (the run_batches.py + run_batches_contracts.py split —
routes/job_batch_operations.py sits inside its growth-allowance band).
"""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from server.app.auth.dependencies import reject_studio_agent_scope
from server.app.jobs import JobQueries
from server.app.routes.job_batch_operations import JobBatchOperationResponse
from server.app.services.job_batch_operations import listing as batch_ops_listing


class JobBatchOperationListItem(JobBatchOperationResponse):
    """任务中心批量操作卡片载荷：#946 操作句柄 + 归档位。

    archive_source：manual = 用户在任务中心手动归档；auto = 终态结束超过
    7 天按查询口径自动归档（不落列）；非归档视图为 null。
    """

    archived_at: str | None
    archive_source: Literal["manual", "auto"] | None


class JobBatchOperationListResponse(BaseModel):
    """单视图一页；active_count 与视图无关（徽标与分区标题同快照）。"""

    operations: list[JobBatchOperationListItem]
    next_cursor: str | None = Field(
        description="keyset 游标（ended/archived 瀑布流）；active 视图不分页恒为 null"
    )
    active_count: int = Field(description="进行中 + 排队数（徽标口径）")


class JobBatchOperationArchiveResponse(BaseModel):
    """手动归档回包：更新后的卡片载荷（幂等：重复归档返回现态，不报错）。"""

    operation: JobBatchOperationListItem


def create_job_batch_operations_listing_router(job_db: JobQueries) -> APIRouter:
    router = APIRouter()

    @router.get(
        "/workspaces/{workspace_id}/job-batch-operations",
        response_model=JobBatchOperationListResponse,
    )
    def list_job_batch_operations(
        workspace_id: str,
        view: Literal["active", "ended", "archived"] = "active",
        limit: Annotated[int, Query(ge=1, le=200)] = 20,
        cursor: str | None = None,
    ) -> JobBatchOperationListResponse:
        return JobBatchOperationListResponse(
            **batch_ops_listing.list_job_batch_operations(
                job_db, workspace_id, view=view, limit=limit, cursor=cursor
            )
        )

    @router.post(
        "/workspaces/{workspace_id}/job-batch-operations/{op_id}/archive",
        response_model=JobBatchOperationArchiveResponse,
        dependencies=[Depends(reject_studio_agent_scope)],
    )
    def archive_job_batch_operation(
        workspace_id: str, op_id: str
    ) -> JobBatchOperationArchiveResponse:
        return JobBatchOperationArchiveResponse(
            **batch_ops_listing.archive_job_batch_operation(job_db, workspace_id, op_id)
        )

    return router
