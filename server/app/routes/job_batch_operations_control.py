"""Pause/resume/cancel controls for asynchronous batch operations (#1397)."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Depends

from server.app.auth.dependencies import reject_studio_agent_scope
from server.app.jobs import JobQueries
from server.app.routes.job_batch_operations import JobBatchOperationResponse
from server.app.routes.job_http import raise_job_http_error
from server.app.services.job_batch_operations import (
    cancel_job_batch_operation,
    operation_record,
    pause_job_batch_operation,
    resume_job_batch_operation,
)
from server.app.services.job_errors import JobServiceError

Control = Callable[[JobQueries, str, str], dict[str, Any]]


def create_job_batch_operations_control_router(job_db: JobQueries) -> APIRouter:
    router = APIRouter(dependencies=[Depends(reject_studio_agent_scope)])

    def control(workspace_id: str, op_id: str, action: Control) -> JobBatchOperationResponse:
        try:
            operation = action(job_db, workspace_id, op_id)
        except JobServiceError as error:
            raise_job_http_error(error)
        return JobBatchOperationResponse.model_validate(operation_record(job_db, operation))

    @router.post(
        "/workspaces/{workspace_id}/job-batch-operations/{op_id}/pause",
        response_model=JobBatchOperationResponse,
    )
    def pause_batch_operation(workspace_id: str, op_id: str) -> JobBatchOperationResponse:
        return control(workspace_id, op_id, pause_job_batch_operation)

    @router.post(
        "/workspaces/{workspace_id}/job-batch-operations/{op_id}/resume",
        response_model=JobBatchOperationResponse,
    )
    def resume_batch_operation(workspace_id: str, op_id: str) -> JobBatchOperationResponse:
        return control(workspace_id, op_id, resume_job_batch_operation)

    @router.post(
        "/workspaces/{workspace_id}/job-batch-operations/{op_id}/cancel",
        response_model=JobBatchOperationResponse,
    )
    def cancel_batch_operation(workspace_id: str, op_id: str) -> JobBatchOperationResponse:
        return control(workspace_id, op_id, cancel_job_batch_operation)

    return router
