from __future__ import annotations

from typing import Any

from server.app.jobs.queries.job_filtering import (
    JobListFilter,
    count_jobs_filtered,
    job_facets,
)
from server.app.jobs.queries.job_pagination import list_jobs_paginated
from server.app.services.job_patch_queries import JobPatchQueryService
from server.app.services.job_patch_query_summaries import summarize_paginated_jobs


class JobListQueryService(JobPatchQueryService):
    """Filtered/paginated job list queries for the workspace job list view."""

    def page(
        self,
        workspace_id: str,
        job_filter: JobListFilter,
        limit: int = 200,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        # Sample the revision watermark BEFORE reading jobs: events recorded
        # after this point carry a higher revision, so the client's patches
        # always apply on top of this page instead of being dropped as
        # already-covered while the page actually missed the change.
        revision = self._job_event_buffer.current_revision() if self._job_event_buffer else 0
        jobs, next_cursor = list_jobs_paginated(
            self.job_db, workspace_id, limit, cursor, job_filter
        )
        jobs = summarize_paginated_jobs(self, self.job_db, jobs)
        # The filtered total and job stats are only consumed from the first
        # page; computing them on cursor pages repeats workspace-wide
        # aggregations for no benefit.
        total = (
            count_jobs_filtered(self.job_db, workspace_id, job_filter) if cursor is None else None
        )
        stats = self.job_db.count_jobs_by_status(workspace_id) if cursor is None else {}
        return {
            "workspace_id": workspace_id,
            "revision": revision,
            "total": total,
            "stats": stats,
            "jobs": jobs,
            "next_cursor": next_cursor,
        }

    def facets(self, workspace_id: str, job_filter: JobListFilter) -> dict[str, Any]:
        raw = job_facets(self.job_db, workspace_id, job_filter)
        return {
            "workspace_id": workspace_id,
            "total": raw["total"],
            "status_counts": raw["status_counts"],
            # Version keys are stringified; the null version is keyed "none".
            "version_counts": {
                ("none" if version is None else str(version)): count
                for version, count in raw["version_counts"].items()
            },
            # Jobs without an active node are keyed "" (empty string).
            "node_counts": {
                (node_key or ""): count for node_key, count in raw["node_counts"].items()
            },
        }
