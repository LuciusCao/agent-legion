"""Latest-failed-run queries over node_runs for failure classification."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any

from server.app.jobs.queries.base import JobQueriesBase


class FailedNodeRunQueriesMixin(JobQueriesBase):
    def list_failed_node_runs(
        self,
        workspace_id: str,
        *,
        category: str | None = None,
        detail: str | None = None,
        workflow_key: str | None = None,
        since: datetime | None = None,
        job_ids: Sequence[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Latest run per (job_id, node_key) that is failed, newest first.

        Filters apply to the latest run only: a node that recovered (or failed
        again under a different category) after an older matching failure is
        not returned. ``job_ids`` (when non-empty) scopes the window scan to
        those jobs — same semantics as filtering the result, but the window
        only walks those jobs' runs instead of the whole workspace.
        """
        inner_clauses = ["jobs.workspace_id = %s"]
        params: list[Any] = [workspace_id]
        if workflow_key:
            inner_clauses.append("jobs.workflow_key = %s")
            params.append(workflow_key)
        if job_ids:
            placeholders = ",".join("%s" for _ in job_ids)
            inner_clauses.append(f"node_runs.job_id in ({placeholders})")
            params.extend(str(job_id) for job_id in job_ids)
        outer_clauses = ["latest.rn = 1", "latest.status = 'failed'"]
        if category:
            outer_clauses.append("latest.failure_category = %s")
            params.append(category)
        if detail:
            outer_clauses.append("latest.failure_detail = %s")
            params.append(detail)
        if since is not None:
            outer_clauses.append("latest.finished_at >= %s")
            params.append(since)
        inner_where = " and ".join(inner_clauses)
        outer_where = " and ".join(outer_clauses)
        with self._connect_read() as conn:
            rows = conn.execute(
                f"""
                select
                  latest.node_run_id,
                  latest.job_id,
                  latest.node_key,
                  latest.workflow_key,
                  latest.failure_category,
                  latest.failure_detail,
                  latest.error_message,
                  latest.finished_at
                from (
                  select
                    node_runs.id as node_run_id,
                    node_runs.job_id,
                    node_runs.node_key,
                    node_runs.status,
                    node_runs.failure_category,
                    node_runs.failure_detail,
                    node_runs.error_message,
                    node_runs.finished_at,
                    jobs.workflow_key,
                    row_number() over (
                      partition by node_runs.job_id, node_runs.node_key
                      order by node_runs.id desc
                    ) as rn
                  from node_runs
                  join jobs on jobs.id = node_runs.job_id
                  where {inner_where}
                ) latest
                where {outer_where}
                order by latest.finished_at desc, latest.node_run_id desc
                """,
                params,
            )
            return [dict(row) for row in rows]
