from typing import Never

from fastapi import HTTPException

from server.app.services.job_errors import (
    ConflictError,
    CustomNodesDisabledError,
    DraftWorkflowKeyMismatchError,
    InvalidOperationError,
    JobServiceError,
    NotFoundError,
    UnsupportedOperationError,
)
from server.app.services.job_log_raw import PayloadTooLargeError
from server.app.services.job_operation_error import JobOperationError
from server.app.settings import Settings


def require_workflows_enabled(settings: Settings) -> None:
    if not settings.executor_runtime.workflows.enabled:
        raise HTTPException(status_code=404, detail="Workflows are disabled")


def raise_job_http_error(error: JobServiceError) -> Never:
    if isinstance(error, NotFoundError):
        raise HTTPException(status_code=404, detail=str(error)) from error
    if isinstance(error, CustomNodesDisabledError):
        raise HTTPException(status_code=403, detail=str(error)) from error
    if isinstance(error, ConflictError):
        raise HTTPException(status_code=409, detail=str(error)) from error
    if isinstance(error, UnsupportedOperationError):
        raise HTTPException(status_code=501, detail=str(error)) from error
    if isinstance(error, PayloadTooLargeError):
        raise HTTPException(status_code=413, detail=str(error)) from error
    if isinstance(error, DraftWorkflowKeyMismatchError):
        raise HTTPException(status_code=422, detail=str(error)) from error
    if isinstance(error, InvalidOperationError):
        raise HTTPException(status_code=400, detail=str(error)) from error
    raise error


def raise_job_operation_error(error: JobOperationError) -> Never:
    """Map a failed/skipped single-job mutation to its HTTP error."""
    status_code = 404 if error.reason_code in ("not_found", "node_not_found") else 400
    raise HTTPException(status_code=status_code, detail=error.failure_detail) from error
