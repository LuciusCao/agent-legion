"""Query service for failure classification views over node_runs."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from server.app.jobs import JobQueries


class FailedNodeRunQueryService:
    def __init__(self, job_db: JobQueries) -> None:
        self.job_db = job_db

    def list_failed_node_runs(
        self,
        workspace_id: str,
        *,
        category: str | None = None,
        detail: str | None = None,
        workflow_key: str | None = None,
        since: datetime | None = None,
    ) -> list[dict[str, Any]]:
        return self.job_db.list_failed_node_runs(
            workspace_id,
            category=category,
            detail=detail,
            workflow_key=workflow_key,
            since=since,
        )
