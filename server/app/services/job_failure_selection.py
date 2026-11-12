"""Bounded latest-failure job selection shared by sync and async rerun paths."""

from __future__ import annotations

from typing import Any

from server.app.jobs import JobQueries


def resolve_failed_job_ids_page(
    job_db: JobQueries,
    workspace_id: str,
    category: str,
    limit: int,
    *,
    cursor: str | None = None,
    freeze_at: str | None = None,
) -> tuple[list[str], str | None, str | None]:
    """Page distinct latest-failure job ids with the jobs freeze protocol."""
    rows = job_db.list_failed_job_ids_page(
        workspace_id,
        category=category,
        limit=limit + 1,
        before=cursor,
        freeze_at=freeze_at,
    )
    page = rows[:limit]
    next_cursor = _failed_run_cursor(page[-1]) if len(rows) > limit else None
    if freeze_at is None and cursor is None and page:
        freeze_at = _failed_run_cursor(page[0])
    return [str(row["job_id"]) for row in page], next_cursor, freeze_at


def _failed_run_cursor(row: dict[str, Any]) -> str:
    finished_at = row["finished_at"]
    return f"{'' if finished_at is None else finished_at}|{row['node_run_id']}"
