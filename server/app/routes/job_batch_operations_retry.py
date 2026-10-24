"""Retry-failed endpoint for terminal batch operations (#1358, M52 复评遗留).

``POST /workspaces/{id}/job-batch-operations/{op_id}/retry-failed`` — 终态原
op 的失败项按原 kind/参数续跑为一个新 operation（服务端派生，语义与拒绝
矩阵见 ``services/job_batch_operations/retry.py``）。契约复用 #946 的 202
受理句柄（永远异步，无同步快路径）。挂在 job route group（member 级
``require_job_workspace_access``），与归档端点一样叠加
``reject_studio_agent_scope``（STUDIO-AGENT-001）。
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends

from server.app.auth.dependencies import reject_studio_agent_scope, require_user
from server.app.jobs import JobQueries
from server.app.routes.job_batch_operations import (
    JobBatchOperationAcceptedResponse,
    JobBatchOperationResponse,
)
from server.app.routes.job_http import raise_job_http_error
from server.app.services.job_batch_operations import operation_record, submit_retry_failed
from server.app.services.job_errors import JobServiceError


def create_job_batch_operations_retry_router(job_db: JobQueries) -> APIRouter:
    router = APIRouter(dependencies=[Depends(reject_studio_agent_scope)])

    @router.post(
        "/workspaces/{workspace_id}/job-batch-operations/{op_id}/retry-failed",
        status_code=202,
        response_model=JobBatchOperationAcceptedResponse,
    )
    def retry_failed_batch_operation(
        workspace_id: str,
        op_id: str,
        user: Annotated[dict[str, Any], Depends(require_user)],
    ) -> JobBatchOperationAcceptedResponse:
        try:
            operation = submit_retry_failed(
                job_db, workspace_id, op_id, created_by=f"user:{user['id']}"
            )
        except JobServiceError as error:
            raise_job_http_error(error)
        return JobBatchOperationAcceptedResponse(
            operation=JobBatchOperationResponse.model_validate(operation_record(job_db, operation))
        )

    return router
