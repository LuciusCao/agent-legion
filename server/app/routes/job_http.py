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
from server.app.services.skill_editing import SkillEditValidationError


def reject_mismatched_workflow_key(workspace_id: str, workflow_key: str | None) -> None:
    """#211 Phase 3 read-binding guard (mirrors #299's URL-alias guard): a
    request body's deprecated workflow_key equals the workspace id (v62
    binding). With predicates binding workspace_id alone, a mismatched key
    cannot narrow any lookup and must not flow into rows — reject with 400.
    """
    if workflow_key not in (None, workspace_id):
        raise HTTPException(
            status_code=400,
            detail="workflow_key must equal the workspace id (schema v62)",
        )


# ``require_workflows_enabled`` retired (#385/#389): the gray-release 404
# gate covered the entire core API surface with no legitimate off state in
# single-node deployments; the API plane is now always available and the
# deployment shape is expressed by code_capacity (0 = pure-remote).


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
    if isinstance(error, SkillEditValidationError):
        raise HTTPException(
            status_code=422, detail={"message": str(error), "errors": error.errors}
        ) from error
    if isinstance(error, InvalidOperationError):
        raise HTTPException(status_code=400, detail=str(error)) from error
    raise error


def raise_job_operation_error(error: JobOperationError) -> Never:
    """Map a failed/skipped single-job mutation to its HTTP error."""
    status_code = 404 if error.reason_code in ("not_found", "node_not_found") else 400
    raise HTTPException(status_code=status_code, detail=error.failure_detail) from error
