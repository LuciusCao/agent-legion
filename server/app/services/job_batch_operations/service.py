"""Batch job operations async queueing (#946) — accept side.

Phase 4 of the runs-async milestone (docs/architecture/batch-ops-async.md;
the runs/items precedent is docs/architecture/runs-async-mode.md #1117).

The 9 batch job endpoints (rerun / rerun-by-failure / run-to /
pause / resume / upgrade-workflow / package / clear-packed and the batch
removal) execute synchronously for small selections (unchanged fast path,
byte-identical responses) and enqueue a background task for selections above
``BATCH_OPS_ASYNC_THRESHOLD``: the request persists one
``job_batch_operations`` row (schema v101) with the frozen, normalized target
ids + kind params and returns 202 with the handle; the in-process feeder
(``job_batch_operations.feeder``) walks the cursor, executing each job
through the SAME per-job service functions the sync path calls, so per-job
result semantics (succeeded/skipped/failed + reason_code) and the
write-time guard discipline (EXEC-GENERATION-002 surfaces) are preserved by
construction.

Selection resolution stays in the request thread (one keyset scan, the
existing ``resolve_batch_selection`` — MAX_BATCH_JOBS still caps it, see the
module docstring in ``job_selection_resolver``); only the per-job mutation
loop moves to the feeder. The frozen id list means the feeder never
re-resolves against live filters.
"""

from __future__ import annotations

import json
import os
import uuid
from collections.abc import Collection
from typing import Any

from server.app.db.rowmap import iso_optional
from server.app.jobs import JobQueries
from server.app.jobs.queries.job_batch_operations import (
    JOB_BATCH_OPERATION_KINDS,
    KINDS_DEDUP_SELECTION,
)
from server.app.jobs.queries.job_filtering import JobListFilter
from server.app.services.job_rerun.batch import resolve_failure_rerun_scope
from server.app.services.job_selection_resolver import resolve_batch_selection

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


def submit_operation(
    job_db: JobQueries,
    workspace_id: str,
    kind: str,
    ids: list[str],
    params: dict[str, Any],
    created_by: str,
) -> dict[str, Any]:
    """Persist one accepted operation row; returns the stored row (the handle)."""
    if kind not in JOB_BATCH_OPERATION_KINDS:
        raise ValueError(f"unknown batch operation kind: {kind!r}")
    frozen = _freeze_ids(kind, ids)
    op_id = uuid.uuid4().hex
    job_db.create_job_batch_operation(
        op_id,
        workspace_id,
        kind,
        {"job_ids": frozen, "params": params},
        len(frozen),
        created_by,
    )
    row = job_db.get_job_batch_operation(op_id)
    if row is None:
        raise RuntimeError("job batch operation row missing right after its write")
    return row


def _freeze_ids(kind: str, ids: list[str]) -> list[str]:
    """Freeze the selection the way the kind's sync loop would consume it."""
    if kind in KINDS_DEDUP_SELECTION:
        return list(dict.fromkeys(value.strip() for value in ids if value.strip()))
    return list(ids)


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
    """Resolve the selection, then dispatch: (ids, operation row | None).

    Shared by every async-capable batch endpoint: resolution (with its
    fail-fast 422/empty-selection semantics) always runs in the request
    thread; the operation row exists only above the threshold. The sync
    branch consumes ``ids`` as explicit job ids — resolution of an explicit
    list is a passthrough, so the per-job outcomes are unchanged.
    """
    ids = resolve_batch_selection(job_db, workspace_id, job_ids, job_filter, exclude_ids)
    operation = submit_operation_if_large(job_db, workspace_id, kind, ids, params, created_by)
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
    """rerun-by-failure's dispatch: the scope resolution (explicit ids /
    filter / every matching failure) is shared with the sync service
    (``job_rerun.batch.resolve_failure_rerun_scope``) so both modes freeze
    the same target set.
    """
    _requested, scope = resolve_failure_rerun_scope(
        job_db, workspace_id, category, job_ids, job_filter, exclude_ids
    )
    operation = submit_operation_if_large(
        job_db,
        workspace_id,
        "rerun_by_failure",
        scope,
        {"category": category, "strategy": strategy, "from_node_key": from_node_key},
        created_by,
    )
    return scope, operation


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
