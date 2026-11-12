"""Batch job operations async queueing (#946) — accept side.

Phase 4 of the runs-async milestone (docs/architecture/batch-ops-async.md;
the runs/items precedent is docs/architecture/runs-async-mode.md #1117).

The 11 batch job endpoints (the original nine plus batch-approve/reject)
execute synchronously for small selections and enqueue a background task for selections above
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

import os
from collections.abc import Collection
from typing import Any

from server.app.jobs import JobQueries
from server.app.jobs.queries.job_batch_operations import JOB_BATCH_OPERATION_KINDS
from server.app.jobs.queries.job_filtering import JobListFilter
from server.app.services.job_batch_operations.records import (
    get_operation_for_workspace as get_operation_for_workspace,
)
from server.app.services.job_batch_operations.records import (
    operation_record as operation_record,
)
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
    *,
    operation_id: str | None = None,
) -> dict[str, Any] | None:
    """None = stay on the synchronous fast path; else the 202 handle row."""
    if not should_enqueue(len(ids)):
        return None
    return submit_operation(
        job_db, workspace_id, kind, ids, params, created_by, operation_id=operation_id
    )


def _submit_dispatched(
    job_db: JobQueries,
    workspace_id: str,
    kind: str,
    ids: list[str],
    params: dict[str, Any],
    created_by: str,
    stream: dict[str, Any] | None,
    operation_id: str | None,
) -> dict[str, Any] | None:
    if stream is not None:
        return submit_operation(
            job_db,
            workspace_id,
            kind,
            ids,
            params,
            created_by,
            stream=stream,
            operation_id=operation_id,
        )
    return submit_operation_if_large(
        job_db, workspace_id, kind, ids, params, created_by, operation_id=operation_id
    )


def dispatch_batch_selection(
    job_db: JobQueries,
    workspace_id: str,
    kind: str,
    job_ids: list[str] | None,
    job_filter: JobListFilter | None,
    exclude_ids: Collection[str],
    params: dict[str, Any],
    created_by: str,
    *,
    operation_id: str | None = None,
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
    operation = _submit_dispatched(
        job_db, workspace_id, kind, ids, params, created_by, stream, operation_id
    )
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
        job_db, workspace_id, "rerun_by_failure", ids, params, created_by, stream, None
    )
    return ids, operation
