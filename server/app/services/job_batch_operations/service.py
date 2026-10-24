"""Batch job operations async queueing (#946) — accept side.

Phase 4 of the runs-async milestone (docs/architecture/batch-ops-async.md;
the runs/items precedent is docs/architecture/runs-async-mode.md #1117).

The 9 batch job endpoints (rerun / rerun-by-failure / run-to /
pause / resume / upgrade-workflow / package / clear-packed and the batch
removal) execute synchronously for small selections (unchanged fast path,
byte-identical responses) and enqueue a background task for selections above
``BATCH_OPS_ASYNC_THRESHOLD``: the request persists one
``job_batch_operations`` row (schema v101) with the frozen target spec and
returns 202 with the handle; the in-process feeder
(``job_batch_operations.feeder``) walks the cursor, executing each job
through the SAME per-job service functions the sync path calls, so per-job
result semantics (succeeded/skipped/failed + reason_code) and the
write-time guard discipline (EXEC-GENERATION-002 surfaces) are preserved by
construction.

Explicit ids freeze in full. Filter and unrestricted-failure selections read
at most threshold+1 ids to choose the sync/async path; larger ones freeze the
filter and a keyset upper bound, then the feeder resolves bounded buffers.
"""

from __future__ import annotations

import json
import os
from collections.abc import Collection
from typing import Any

from server.app.db.rowmap import iso_optional
from server.app.jobs import JobQueries
from server.app.jobs.queries.job_batch_operations import JOB_BATCH_OPERATION_KINDS
from server.app.jobs.queries.job_filtering import JobListFilter
from server.app.services.job_batch_operations.selection import (
    prepare_batch_selection,
    prepare_failure_selection,
    submit_operation,
)

# The kind vocabulary is canonical in the queries layer (next to the DDL);
# services only re-export it for route typing.
__all__ = [
    "BATCH_OPS_ASYNC_THRESHOLD",
    "DEFAULT_BATCH_OPS_ASYNC_THRESHOLD",
    "JOB_BATCH_OPERATION_KINDS",
    "dispatch_batch_selection",
    "dispatch_rerun_by_failure",
    "get_operation_for_workspace",
    "operation_record",
    "should_enqueue",
    "submit_operation",
    "submit_operation_if_large",
]

BATCH_OPS_ASYNC_THRESHOLD_ENV = "AGENT_LEGION_BATCH_OPS_ASYNC_THRESHOLD"
# Selections at/below this stay on the synchronous fast path (aligned with
# the #1157 phase0/phase3 1000-item async switch). Above it the request
# returns 202 + handle and the feeder drives the mutations.
DEFAULT_BATCH_OPS_ASYNC_THRESHOLD = 1000


def _batch_ops_async_threshold() -> int:
    """Env override for the async switch (import-time read; restart to change)."""
    try:
        return max(0, int(os.environ.get(BATCH_OPS_ASYNC_THRESHOLD_ENV, "")))
    except ValueError:
        return DEFAULT_BATCH_OPS_ASYNC_THRESHOLD


BATCH_OPS_ASYNC_THRESHOLD = _batch_ops_async_threshold()


def should_enqueue(selection_size: int) -> bool:
    return selection_size > BATCH_OPS_ASYNC_THRESHOLD


def submit_operation_if_large(
    job_db: JobQueries,
    workspace_id: str,
    kind: str,
    ids: list[str],
    params: dict[str, Any],
    created_by: str,
) -> dict[str, Any] | None:
    """None = stay on the synchronous fast path; else the 202 handle row."""
    if not should_enqueue(len(ids)):
        return None
    return submit_operation(job_db, workspace_id, kind, ids, params, created_by)


def _submit_dispatched(
    job_db: JobQueries,
    workspace_id: str,
    kind: str,
    ids: list[str],
    params: dict[str, Any],
    created_by: str,
    stream: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if stream is not None:
        return submit_operation(job_db, workspace_id, kind, ids, params, created_by, stream=stream)
    return submit_operation_if_large(job_db, workspace_id, kind, ids, params, created_by)


def dispatch_batch_selection(
    job_db: JobQueries,
    workspace_id: str,
    kind: str,
    job_ids: list[str] | None,
    job_filter: JobListFilter | None,
    exclude_ids: Collection[str],
    params: dict[str, Any],
    created_by: str,
) -> tuple[list[str], dict[str, Any] | None]:
    """Resolve a bounded prefix, then choose the sync or async path."""
    ids, stream = prepare_batch_selection(
        job_db,
        workspace_id,
        job_ids,
        job_filter,
        exclude_ids,
        BATCH_OPS_ASYNC_THRESHOLD,
    )
    operation = _submit_dispatched(job_db, workspace_id, kind, ids, params, created_by, stream)
    return ids, operation


def dispatch_rerun_by_failure(
    job_db: JobQueries,
    workspace_id: str,
    *,
    category: str,
    strategy: str,
    from_node_key: str | None,
    job_ids: list[str],
    job_filter: JobListFilter | None,
    exclude_ids: Collection[str],
    created_by: str,
) -> tuple[list[str], dict[str, Any] | None]:
    """Dispatch explicit/filter ids, or stream every matching failure."""
    ids, stream = prepare_failure_selection(
        job_db,
        workspace_id,
        category,
        job_ids,
        job_filter,
        exclude_ids,
        BATCH_OPS_ASYNC_THRESHOLD,
    )
    params = {"category": category, "strategy": strategy, "from_node_key": from_node_key}
    operation = _submit_dispatched(
        job_db, workspace_id, "rerun_by_failure", ids, params, created_by, stream
    )
    return ids, operation


def operation_record(
    job_db: JobQueries, row: dict[str, Any], *, reason_counts: dict[str, int] | None = None
) -> dict[str, Any]:
    """Public operation handle; target_spec (frozen ids) stays internal.

    ``reason_counts`` lets the listing surface pass a page-wide precomputed
    breakdown (one grouped query per page); None falls back to the per-row
    single-op query (the status poll path).
    """
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
        # reason_code breakdown over non-succeeded results, computed on read
        # (the counters stay crash-consistent via the per-advance recompute).
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
