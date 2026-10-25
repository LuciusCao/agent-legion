"""Batch-ops task-center listing/archive service (M61).

The service half of the batch-ops read/archive surface: view semantics
(active/ended/archived + the query-time auto-archive rule), keyset cursor
codec, and the guarded manual archive — the ``run_batch_listing`` (#1157
phase3) shape mapped onto job_batch_operations so the Studio task center can
mix both card families in one three-view layout. SQL lives in
``jobs/queries/job_batch_operations_listing.py`` (BOUNDARY-DATA-001); the
contracts in ``routes/job_batch_operations_listing.py``.

One deliberate divergence from run_batches: the archive endpoint is
idempotent (M61 brief) — a repeat archive returns the current state instead
of run_batches' 409. The guarded mutation's WHERE clause stays the single
arbitration point; the replay is a post-miss re-read, never a blind success.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from server.app.db.rowmap import iso_optional
from server.app.jobs import JobQueries
from server.app.jobs.queries.job_batch_operations_listing import (  # fmt: skip
    AUTO_ARCHIVE_DAYS,
    job_batch_operation_listing_queries_from_dsn,
)
from server.app.services.job_batch_operations.feeding_gate import operation_feeding_gate
from server.app.services.job_batch_operations.service import operation_record
from server.app.services.job_errors import ConflictError, InvalidOperationError, NotFoundError
from server.app.services.job_intake_workspace import get_workspace
from server.app.services.run_batches.feeding_gate import feeding_gate_snapshot
from server.app.settings import Settings

JobBatchOperationView = Literal["active", "ended", "archived"]
_VIEWS: tuple[str, ...] = ("active", "ended", "archived")

# Archived job_batch_operations are removed 90 days after archiving (manual:
# archived_at; auto-archive is a query-time reading, so the auto arm's cutoff
# reaches back AUTO_ARCHIVE_DAYS further). Unconditional — the same
# task-center product promise as run_batches (services/execution_retention.py
# calls both arms on every sweep).
ARCHIVED_JOB_BATCH_OPERATION_RETENTION_DAYS = 90
_SWEEP_BATCH_SIZE = 500


def sweep_archived_job_batch_operations(connect_source: Any, moment: datetime) -> int:
    """Remove archived job_batch_operations past the 90-day archive retention.

    Same discipline as ``run_batch_listing.sweep_archived_run_batches``: the
    batched loop lives in the queries layer, each batch its own short
    transaction, the removal itself is the progress. ``connect_source`` is
    the #187 ConnectSource contract (JobQueries facade or bare DSN).
    """
    queries = (
        job_batch_operation_listing_queries_from_dsn(connect_source)
        if isinstance(connect_source, str)
        else connect_source
    )
    return int(
        queries.sweep_expired_archived_job_batch_operations(
            manual_cutoff=moment - timedelta(days=ARCHIVED_JOB_BATCH_OPERATION_RETENTION_DAYS),
            auto_cutoff=moment
            - timedelta(days=ARCHIVED_JOB_BATCH_OPERATION_RETENTION_DAYS + AUTO_ARCHIVE_DAYS),
            batch_size=_SWEEP_BATCH_SIZE,
        )
    )


def _as_utc(value: Any) -> datetime:
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def operation_list_item(
    job_db: JobQueries,
    row: dict[str, Any],
    *,
    now: datetime,
    reason_counts: dict[str, int] | None = None,
    gate: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """The task-center card payload: the #946 operation handle plus the
    archive fields (归档时间 + 来源标注 manual/auto), exactly the run_batches
    card rule."""
    item = {**operation_record(job_db, row, reason_counts=reason_counts), **(gate or {})}
    archived_at = iso_optional(row.get("archived_at"))
    finished_at = row.get("finished_at")
    archive_source: str | None = None
    if archived_at is not None:
        archive_source = "manual"
    elif (
        str(row.get("status")) in ("completed", "failed", "cancelled")
        and finished_at is not None
        and _as_utc(finished_at) < now - timedelta(days=AUTO_ARCHIVE_DAYS)
    ):
        archive_source = "auto"
    return {**item, "archived_at": archived_at, "archive_source": archive_source}


def _encode_cursor(sort_at: Any, op_id: str) -> str:
    return f"{_as_utc(sort_at).isoformat()}|{op_id}"


def _decode_cursor(raw: str) -> tuple[datetime, str]:
    parts = raw.rsplit("|", 1)
    if len(parts) != 2 or not parts[0] or not parts[1]:
        raise InvalidOperationError("Invalid cursor")
    try:
        return _as_utc(datetime.fromisoformat(parts[0])), parts[1]
    except ValueError as exc:
        raise InvalidOperationError("Invalid cursor") from exc


def _page_items(
    job_db: JobQueries,
    rows: list[dict[str, Any]],
    *,
    now: datetime,
    gate_snapshot: dict[str, int] | None = None,
) -> list[dict[str, Any]]:
    """One page of cards with a page-wide reason-code breakdown (one grouped
    query, not one per row)."""
    reasons = job_db.summarize_job_batch_operation_reasons_many([str(row["id"]) for row in rows])
    return [
        operation_list_item(
            job_db,
            row,
            now=now,
            reason_counts=reasons.get(str(row["id"]), {}),
            gate=operation_feeding_gate(gate_snapshot, row),
        )
        for row in rows
    ]


def list_job_batch_operations(
    job_db: JobQueries,
    workspace_id: str,
    *,
    settings: Settings,
    view: str,
    limit: int,
    cursor: str | None,
) -> dict[str, Any]:
    """One task-center page. Every view also carries ``active_count`` so the
    badge and the section header read the same snapshot (run_batches 同形)."""
    get_workspace(job_db, workspace_id)
    if view not in _VIEWS:
        raise InvalidOperationError(f"Unknown job-batch-operations view: {view}")
    now = datetime.now(UTC)
    auto_cutoff = now - timedelta(days=AUTO_ARCHIVE_DAYS)
    decoded = _decode_cursor(cursor) if cursor else None
    operations: list[dict[str, Any]] = []
    next_cursor: str | None = None
    if view == "active":
        if decoded is not None:
            raise InvalidOperationError("The active view is not paginated")
        rows = job_db.list_active_job_batch_operations(workspace_id)
        snapshot = feeding_gate_snapshot(job_db, settings, workspace_id) if rows else None
        operations = _page_items(job_db, rows, now=now, gate_snapshot=snapshot)
    elif view == "ended":
        rows = job_db.list_ended_job_batch_operations(
            workspace_id, auto_archive_cutoff=auto_cutoff, cursor=decoded, limit=limit
        )
        operations = _page_items(job_db, rows, now=now)
        if len(rows) == limit and rows[-1].get("finished_at") is not None:
            next_cursor = _encode_cursor(rows[-1]["finished_at"], str(rows[-1]["id"]))
    else:
        rows = job_db.list_archived_job_batch_operations(
            workspace_id, auto_archive_cutoff=auto_cutoff, cursor=decoded, limit=limit
        )
        operations = _page_items(job_db, rows, now=now)
        if len(rows) == limit:
            last = rows[-1]
            sort_at = last.get("archived_at") or last.get("finished_at")
            if sort_at is not None:
                next_cursor = _encode_cursor(sort_at, str(last["id"]))
    return {
        "operations": operations,
        "next_cursor": next_cursor,
        "active_count": job_db.count_active_job_batch_operations(workspace_id),
    }


def archive_job_batch_operation(
    job_db: JobQueries, workspace_id: str, op_id: str
) -> dict[str, Any]:
    """Manual archive, idempotent (M61 brief): a repeat archive returns the
    current state, not an error.

    404 for unknown/cross-workspace ids (revalidated against the row, never
    trusted from the path). The guarded mutation's WHERE clause decides the
    races; on a miss the row is re-read once — already archived (a concurrent
    or repeat archive) replays the current state, anything else
    (non-terminal) is a genuine 409.
    """
    row = job_db.get_job_batch_operation(op_id)
    if row is None or str(row["workspace_id"]) != workspace_id:
        raise NotFoundError("Job batch operation not found")
    if row.get("archived_at") is not None:
        return {"operation": operation_list_item(job_db, row, now=datetime.now(UTC))}
    updated = job_db.archive_job_batch_operation(op_id, workspace_id)
    if updated is None:
        raced = job_db.get_job_batch_operation(op_id)
        if raced is not None and raced.get("archived_at") is not None:
            return {"operation": operation_list_item(job_db, raced, now=datetime.now(UTC))}
        raise ConflictError(f"Job batch operation cannot be archived (status: {row['status']})")
    return {"operation": operation_list_item(job_db, updated, now=datetime.now(UTC))}
