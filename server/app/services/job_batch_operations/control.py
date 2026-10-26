"""Operator control transitions for asynchronous batch operations (#1397)."""

from __future__ import annotations

from typing import Any

from server.app.jobs import JobQueries
from server.app.services.job_batch_operations.records import get_operation_for_workspace
from server.app.services.job_errors import ConflictError, NotFoundError


def _transition(
    job_db: JobQueries,
    workspace_id: str,
    op_id: str,
    *,
    from_statuses: tuple[str, ...],
    to_status: str,
    mismatch: str,
) -> dict[str, Any]:
    operation = get_operation_for_workspace(job_db, workspace_id, op_id)
    if operation is None:
        raise NotFoundError("Job batch operation not found")
    updated = job_db.transition_job_batch_operation(
        op_id, from_statuses=from_statuses, to_status=to_status
    )
    if updated is None:
        raise ConflictError(f"{mismatch} (status: {operation['status']})")
    return updated


def pause_job_batch_operation(job_db: JobQueries, workspace_id: str, op_id: str) -> dict[str, Any]:
    return _transition(
        job_db,
        workspace_id,
        op_id,
        from_statuses=("pending", "running"),
        to_status="paused",
        mismatch="Job batch operation is not feeding",
    )


def resume_job_batch_operation(job_db: JobQueries, workspace_id: str, op_id: str) -> dict[str, Any]:
    return _transition(
        job_db,
        workspace_id,
        op_id,
        from_statuses=("paused",),
        to_status="pending",
        mismatch="Job batch operation is not paused",
    )


def cancel_job_batch_operation(job_db: JobQueries, workspace_id: str, op_id: str) -> dict[str, Any]:
    return _transition(
        job_db,
        workspace_id,
        op_id,
        from_statuses=("pending", "running", "paused"),
        to_status="cancelled",
        mismatch="Job batch operation is already terminal",
    )
