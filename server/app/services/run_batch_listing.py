"""Task-center listing/archive service (#1157 phase3).

The service half of the run-batches read/archive surface: view semantics
(active/ended/archived + the query-time auto-archive rule), keyset cursor
codec, and the guarded manual archive. SQL lives in
``jobs/queries/run_batches_listing.py`` (BOUNDARY-DATA-001); the contracts
in ``routes/run_batches_contracts.py``; design in
docs/architecture/runs-async-mode.md §6.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from server.app.db.rowmap import iso_optional
from server.app.jobs import JobQueries
from server.app.jobs.queries.run_batches_listing import (  # fmt: skip
    AUTO_ARCHIVE_DAYS,
    run_batch_listing_queries_from_dsn,
)
from server.app.services import run_batch
from server.app.services.job_errors import ConflictError, InvalidOperationError, NotFoundError
from server.app.services.job_intake_workspace import get_workspace
from server.app.services.run_batch_feeder import MAX_CHUNK_ERRORS, RUN_BATCH_CHUNK_SIZE
from server.app.services.run_batches.feeding_gate import add_feeding_gate_to_batches
from server.app.settings import Settings

RunBatchView = Literal["active", "ended", "archived"]
_VIEWS: tuple[str, ...] = ("active", "ended", "archived")

# Archived run_batches are removed 90 days after archiving (manual:
# archived_at; auto-archive is a query-time reading, so the auto arm's
# cutoff reaches back AUTO_ARCHIVE_DAYS further). Unconditional — a
# task-center product promise, not gated on execution_retention_days
# (services/execution_retention.py calls this on every sweep).
ARCHIVED_RUN_BATCH_RETENTION_DAYS = 90
_SWEEP_BATCH_SIZE = 500


def sweep_archived_run_batches(connect_source: Any, moment: datetime) -> int:
    """Remove archived run_batches past the 90-day archive retention.

    The batched loop lives in the queries layer (BOUNDARY-DATA-001), each
    batch its own short transaction; the removal itself is the progress, so
    an interrupt simply resumes on the next pass. ``connect_source`` is the
    #187 ConnectSource contract (JobQueries facade or bare DSN).
    """
    queries = (
        run_batch_listing_queries_from_dsn(connect_source)
        if isinstance(connect_source, str)
        else connect_source
    )
    return int(
        queries.sweep_expired_archived_run_batches(
            manual_cutoff=moment - timedelta(days=ARCHIVED_RUN_BATCH_RETENTION_DAYS),
            auto_cutoff=moment
            - timedelta(days=ARCHIVED_RUN_BATCH_RETENTION_DAYS + AUTO_ARCHIVE_DAYS),
            batch_size=_SWEEP_BATCH_SIZE,
        )
    )


def _as_utc(value: Any) -> datetime:
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def batch_list_item(row: dict[str, Any], *, now: datetime) -> dict[str, Any]:
    """The task-center card payload: the #1117 batch handle plus the archive
    fields (归档时间 + 来源标注 manual/auto) and the operator attribution.

    ``failed_item_count`` 由 chunk_errors 推导（reviewer r1 P2-1）：feeder
    的 chunk 容错跳过整个 chunk（RUN_BATCH_CHUNK_SIZE，按 items_total 钳
    制），这批条目不能算「已有任务」跳过；chunk_errors 最多
    MAX_CHUNK_ERRORS 条，打满时真实失败数 ≥ 推导值（lower_bound）。"""
    item = run_batch.batch_record(row)
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
    items_total = int(row.get("items_total") or 0)
    failed_item_count = 0
    for error in item["chunk_errors"]:
        chunk_start = error.get("chunk_start")
        if isinstance(chunk_start, int) and not isinstance(chunk_start, bool):
            failed_item_count += min(RUN_BATCH_CHUNK_SIZE, max(0, items_total - chunk_start))
    return {
        **item,
        "created_by": str(row.get("created_by") or ""),
        "archived_at": archived_at,
        "archive_source": archive_source,
        "failed_item_count": failed_item_count,
        "failed_item_count_lower_bound": len(item["chunk_errors"]) >= MAX_CHUNK_ERRORS,
    }


def _encode_cursor(sort_at: Any, run_id: str) -> str:
    return f"{_as_utc(sort_at).isoformat()}|{run_id}"


def _decode_cursor(raw: str) -> tuple[datetime, str]:
    parts = raw.rsplit("|", 1)
    if len(parts) != 2 or not parts[0] or not parts[1]:
        raise InvalidOperationError("Invalid cursor")
    try:
        return _as_utc(datetime.fromisoformat(parts[0])), parts[1]
    except ValueError as exc:
        raise InvalidOperationError("Invalid cursor") from exc


def list_run_batches(
    job_db: JobQueries,
    workspace_id: str,
    *,
    settings: Settings,
    view: str,
    limit: int,
    cursor: str | None,
) -> dict[str, Any]:
    """One task-center page. Every view also carries ``active_count`` so the
    AppBar badge and the section header read the same snapshot."""
    get_workspace(job_db, workspace_id)
    if view not in _VIEWS:
        raise InvalidOperationError(f"Unknown run-batches view: {view}")
    now = datetime.now(UTC)
    auto_cutoff = now - timedelta(days=AUTO_ARCHIVE_DAYS)
    decoded = _decode_cursor(cursor) if cursor else None
    batches: list[dict[str, Any]] = []
    next_cursor: str | None = None
    if view == "active":
        if decoded is not None:
            raise InvalidOperationError("The active view is not paginated")
        rows = job_db.list_active_run_batches(workspace_id)
        batches = [batch_list_item(row, now=now) for row in rows]
        add_feeding_gate_to_batches(job_db, settings, workspace_id, batches, rows)
    elif view == "ended":
        rows = job_db.list_ended_run_batches(
            workspace_id, auto_archive_cutoff=auto_cutoff, cursor=decoded, limit=limit
        )
        batches = [batch_list_item(row, now=now) for row in rows]
        if len(rows) == limit and rows[-1].get("finished_at") is not None:
            next_cursor = _encode_cursor(rows[-1]["finished_at"], str(rows[-1]["run_id"]))
    else:
        rows = job_db.list_archived_run_batches(
            workspace_id, auto_archive_cutoff=auto_cutoff, cursor=decoded, limit=limit
        )
        batches = [batch_list_item(row, now=now) for row in rows]
        if len(rows) == limit:
            last = rows[-1]
            sort_at = last.get("archived_at") or last.get("finished_at")
            if sort_at is not None:
                next_cursor = _encode_cursor(sort_at, str(last["run_id"]))
    return {
        "batches": batches,
        "next_cursor": next_cursor,
        "active_count": job_db.count_active_run_batches(workspace_id),
    }


def archive_run_batch(job_db: JobQueries, workspace_id: str, run_id: str) -> dict[str, Any]:
    """Manual archive. 404 for unknown/cross-workspace ids (revalidated
    against the row, never trusted from the path); 409 when the guarded
    write misses (non-terminal or already archived — the race is decided by
    the WHERE, so a double-click answers 409, not a stale success)."""
    row = job_db.get_run_batch(run_id)
    if row is None or str(row["workspace_id"]) != workspace_id:
        raise NotFoundError("Run batch not found")
    updated = job_db.archive_run_batch(run_id, workspace_id)
    if updated is None:
        raise ConflictError(
            f"Run batch cannot be archived (status: {row['status']},"
            f" archived: {row.get('archived_at') is not None})"
        )
    return {"batch": batch_list_item(updated, now=datetime.now(UTC))}
