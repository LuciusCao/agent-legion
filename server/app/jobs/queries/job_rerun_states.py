"""Narrow bulk job/node state queries for batch rerun eligibility checks.

Full-row variants (``list_jobs_by_ids`` / ``list_job_nodes_for_jobs``) pay
per-column row materialization for every job; these projections only fetch
the columns the rerun checks read, keeping multi-thousand-job selections
cheap (batch rerun preview).
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from server.app.jobs.queries.base import JobQueriesBase


class JobRerunStateQueriesMixin(JobQueriesBase):
    def list_job_rerun_states_for_jobs(
        self, workspace_id: str, job_ids: Sequence[str]
    ) -> dict[str, dict[str, Any]]:
        """Narrow job rows keyed by id for batch rerun eligibility checks.

        Only the columns the checks read (status/workflow_key plus the
        definition snapshot); a full ``select *`` pays per-column row
        materialization for thousands of jobs.
        """
        if not job_ids:
            return {}
        params = [workspace_id, *(str(job_id) for job_id in job_ids)]
        sql = (
            "select id, status, workflow_key, workflow_definition_snapshot_json"
            f" from jobs where workspace_id=%s and id in ({','.join('%s' for _ in job_ids)})"
        )
        with self._connect_read() as conn:
            rows = conn.execute(sql, params).fetchall()
        return {str(row["id"]): dict(row) for row in rows}

    def list_job_node_states_for_jobs(
        self, job_ids: Sequence[str]
    ) -> dict[str, list[dict[str, Any]]]:
        """Narrow per-job node rows (job_id/node_key/status) for batch checks.

        Same grouping and per-job id ordering as ``list_job_nodes_for_jobs``;
        skipping the wide columns keeps large selections cheap.
        """
        if not job_ids:
            return {}
        placeholders = ",".join("%s" for _ in job_ids)
        grouped: dict[str, list[dict[str, Any]]] = {str(job_id): [] for job_id in job_ids}
        with self._connect_read() as conn:
            rows = conn.execute(
                f"select job_id, node_key, status from job_nodes"
                f" where job_id in ({placeholders}) order by job_id, id",
                [str(job_id) for job_id in job_ids],
            ).fetchall()
        for row in rows:
            grouped[str(row["job_id"])].append(dict(row))
        return grouped
