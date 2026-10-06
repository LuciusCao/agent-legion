"""Query service for failure classification views over node_runs."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from server.app.jobs import JobQueries
from server.app.jobs.queries.failed_run_cursor import (
    format_failed_run_cursor,
    parse_failed_run_cursor,
)


class FailedNodeRunQueryService:
    def __init__(self, job_db: JobQueries) -> None:
        self.job_db = job_db

    def list_failed_node_runs_page(
        self,
        workspace_id: str,
        *,
        limit: int,
        cursor: str | None = None,
        category: str | None = None,
        detail: str | None = None,
        since: datetime | None = None,
    ) -> tuple[list[dict[str, Any]], str | None]:
        """One keyset page (#713): ``limit + 1`` rows decide ``next_cursor``
        without a count; ``cursor`` is a previous page's ``next_cursor``."""
        rows = self.job_db.list_failed_node_runs(
            workspace_id,
            category=category,
            detail=detail,
            since=since,
            limit=limit + 1,
            before=parse_failed_run_cursor(cursor) if cursor else None,
        )
        if len(rows) <= limit:
            return rows, None
        return rows[:limit], format_failed_run_cursor(rows[limit - 1])

    def recent_failures_on_node(
        self, workspace_id: str, node_key: str, *, limit: int
    ) -> list[dict[str, Any]]:
        """Newest ``limit`` latest-failed runs of one node across the
        workspace (bounded by the query, not by slicing the whole list)."""
        return self.job_db.list_failed_node_runs(workspace_id, node_key=node_key, limit=limit)
