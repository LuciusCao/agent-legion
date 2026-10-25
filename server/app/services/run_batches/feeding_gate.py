"""Shared read-only watermark derivation for both task-center feeder families."""

from __future__ import annotations

from typing import Any

from server.app.jobs import JobQueries
from server.app.settings import Settings

RUN_BATCH_WATERMARK = 30_000
WATERMARK_JOB_BATCH_OPERATION_KINDS = frozenset(
    {"rerun", "rerun_by_failure", "run_to", "resume", "upgrade_workflow", "approve"}
)


def run_batch_watermark(settings: Settings) -> int:
    cap = int(settings.executor_runtime.workflows.max_items_per_run or 0)
    return min(RUN_BATCH_WATERMARK, cap) if cap > 0 else RUN_BATCH_WATERMARK


def workspace_backlog(job_db: JobQueries, workspace_id: str) -> int:
    counts = job_db.count_jobs_by_status(workspace_id)
    return int(counts.get("pending", 0)) + int(counts.get("running", 0))


def feeding_gate_snapshot(
    job_db: JobQueries, settings: Settings, workspace_id: str
) -> dict[str, int]:
    return {
        "workspace_backlog": workspace_backlog(job_db, workspace_id),
        "watermark": run_batch_watermark(settings),
    }


def feeding_gate_fields(
    snapshot: dict[str, int],
    *,
    status: str,
    next_index: int,
    items_total: int,
    watermark_applies: bool = True,
) -> dict[str, Any]:
    return {
        **snapshot,
        "feeding_gated": bool(
            watermark_applies
            and status in ("pending", "running")
            and next_index < items_total
            and snapshot["workspace_backlog"] >= snapshot["watermark"]
        ),
    }


def feeding_gate_for_row(
    snapshot: dict[str, int], row: dict[str, Any], *, watermark_applies: bool = True
) -> dict[str, Any]:
    return feeding_gate_fields(
        snapshot,
        status=str(row["status"]),
        next_index=int(row["next_index"]),
        items_total=int(row["items_total"]),
        watermark_applies=watermark_applies,
    )


def add_feeding_gate_to_batches(
    job_db: JobQueries,
    settings: Settings,
    workspace_id: str,
    batches: list[dict[str, Any]],
    rows: list[dict[str, Any]],
) -> None:
    if not rows:
        return
    snapshot = feeding_gate_snapshot(job_db, settings, workspace_id)
    for batch, row in zip(batches, rows, strict=True):
        batch.update(feeding_gate_for_row(snapshot, row))


def batch_with_feeding_gate(
    job_db: JobQueries,
    settings: Settings,
    workspace_id: str,
    batch: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if (
        batch is None
        or batch["status"] not in ("pending", "running")
        or batch["processed_count"] >= batch["items_total"]
    ):
        return batch
    snapshot = feeding_gate_snapshot(job_db, settings, workspace_id)
    return {
        **batch,
        **feeding_gate_fields(
            snapshot,
            status=batch["status"],
            next_index=batch["processed_count"],
            items_total=batch["items_total"],
        ),
    }
