"""Public read models for job batch operation rows."""

from __future__ import annotations

import json
from typing import Any

from server.app.db.rowmap import iso_optional
from server.app.jobs import JobQueries


def operation_record(
    job_db: JobQueries, row: dict[str, Any], *, reason_counts: dict[str, int] | None = None
) -> dict[str, Any]:
    """Public operation handle; target_spec (frozen ids) stays internal."""
    return {
        "id": str(row["id"]),
        "workspace_id": str(row["workspace_id"]),
        "kind": str(row["kind"]),
        "status": str(row["status"]),
        "items_total": int(row["items_total"]),
        "processed_count": int(row["next_index"]),
        "succeeded_count": int(row["succeeded_count"]),
        "skipped_count": int(row["skipped_count"]),
        "failed_count": int(row["failed_count"]),
        "reason_counts": (
            reason_counts
            if reason_counts is not None
            else job_db.summarize_job_batch_operation_reasons(str(row["id"]))
        ),
        "output": json.loads(str(row["output_json"] or "{}")),
        "chunk_errors": json.loads(str(row["chunk_errors_json"] or "[]")),
        "error_message": str(row["error_message"] or ""),
        "created_by": str(row["created_by"] or ""),
        "created_at": iso_optional(row["created_at"]),
        "updated_at": iso_optional(row["updated_at"]),
        "finished_at": iso_optional(row["finished_at"]),
    }


def get_operation_for_workspace(
    job_db: JobQueries, workspace_id: str, op_id: str
) -> dict[str, Any] | None:
    """Workspace binding is re-validated against the row (cross-workspace ids 404)."""
    row = job_db.get_job_batch_operation(op_id)
    if row is None or str(row["workspace_id"]) != workspace_id:
        return None
    return row
