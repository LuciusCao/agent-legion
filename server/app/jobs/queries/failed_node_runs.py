"""Latest-failed-run queries over node_runs for failure classification.

#713: "latest run per (job_id, node_key) is failed" is an anti-join — a failed
run with no newer run of the same (job, node) — not a ``row_number()`` window
over every run of the workspace. The anti-join starts from failed runs only
(``idx_node_runs_status_finished_at_id`` walks them newest-first, so a
``limit`` stops the scan early) and probes newer runs per job through
``idx_node_runs_job_id``; the old window materialized the workspace's whole
run history before filtering, linear in a table that is never pruned.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any

from server.app.jobs.queries.connection import ConnectionQueriesMixin
from server.app.jobs.queries.failed_node_runs_sql import FailedRunCursor, latest_failed_runs_sql


class FailedNodeRunQueriesMixin(ConnectionQueriesMixin):
    def list_failed_node_runs(
        self,
        workspace_id: str,
        *,
        category: str | None = None,
        detail: str | None = None,
        workflow_key: str | None = None,
        since: datetime | None = None,
        job_ids: Sequence[str] | None = None,
        node_key: str | None = None,
        limit: int | None = None,
        before: FailedRunCursor | None = None,
    ) -> list[dict[str, Any]]:
        """Latest run per (job_id, node_key) that is failed, newest first.

        Filters apply to the latest run only: a node that recovered (or failed
        again under a different category) after an older matching failure is
        not returned. ``job_ids`` (when non-empty) scopes the scan to those
        jobs. ``limit`` / ``before`` page the result by the
        ``(finished_at desc, node_run_id desc)`` keyset (#713); unbounded
        reads are only for callers that already bound the job set.
        """
        latest_sql, params = latest_failed_runs_sql(
            workspace_id,
            category=category,
            detail=detail,
            since=since,
            job_ids=job_ids,
            node_key=node_key,
            before=before,
        )
        limit_sql = ""
        if limit is not None:
            limit_sql = " limit %s"
            params.append(limit)
        with self._connect_read() as conn:
            rows = conn.execute(
                f"""
                select
                  latest.id as node_run_id,
                  latest.job_id,
                  latest.node_key,
                  latest.failure_category,
                  latest.failure_detail,
                  latest.error_message,
                  latest.finished_at
                {latest_sql}
                order by latest.finished_at desc, latest.id desc{limit_sql}
                """,
                params,
            )
            return [dict(row) for row in rows]

    def list_failed_job_ids(self, workspace_id: str, *, category: str, limit: int) -> list[str]:
        """Distinct job ids whose latest run of some node failed with ``category``.

        Same latest-run semantics as ``list_failed_node_runs`` but returns at
        most ``limit`` ids (#712 review P1): the unrestricted rerun-by-failure
        selection asks for cap + 1 to decide "too many" without materializing
        every matching failed run of the workspace.
        """
        latest_sql, params = latest_failed_runs_sql(workspace_id, category=category)
        with self._connect_read() as conn:
            rows = conn.execute(
                f"select distinct latest.job_id {latest_sql} limit %s", [*params, limit]
            ).fetchall()
        return [str(row["job_id"]) for row in rows]
