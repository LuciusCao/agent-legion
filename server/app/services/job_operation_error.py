"""Exception form of the legacy per-job mutation result dict.

Single-job mutations (rerun/delete/run-to/continue) raise ``JobOperationError``
instead of returning a ``{"status": "failed", ...}`` dict. Batch callers
reconstruct per-item result dicts via ``to_result``; the route layer maps the
failure to an HTTP status from ``reason_code``.
"""

from __future__ import annotations

from typing import TypedDict

from server.app.services.job_errors import JobServiceError

_OPERATION_FAILURE_LABELS = {
    "rerun": "Rerun failed",
    "delete": "Delete failed",
    "run_to": "Run-to failed",
    "continue": "Continue failed",
}


class JobOperationResult(TypedDict):
    job_id: str
    operation: str
    status: str
    node_key: str | None
    reason_code: str | None
    message: str | None


class JobOperationError(JobServiceError):
    """Non-succeeded (failed/skipped) outcome of a single-job mutation.

    Carries the result-dict fields so batch callers can rebuild per-item
    results and routes can derive the HTTP status and detail.
    """

    def __init__(
        self,
        job_id: str,
        operation: str,
        status: str,
        node_key: str | None = None,
        reason_code: str | None = None,
        message: str | None = None,
    ) -> None:
        super().__init__(message or reason_code or status)
        self.job_id = job_id
        self.operation = operation
        self.status = status
        self.node_key = node_key
        self.reason_code = reason_code
        self.message = message

    @property
    def failure_detail(self) -> str:
        """HTTP detail matching the legacy ``message or reason_code or label`` rule."""
        return (
            self.message
            or self.reason_code
            or _OPERATION_FAILURE_LABELS.get(self.operation, "Operation failed")
        )

    def to_result(self) -> JobOperationResult:
        return {
            "job_id": self.job_id,
            "operation": self.operation,
            "status": self.status,
            "node_key": self.node_key,
            "reason_code": self.reason_code,
            "message": self.message,
        }
