"""Narrow bulk job/node state queries for batch rerun eligibility checks.

Full-row variants (``list_jobs_by_ids`` / ``list_job_nodes_for_jobs``) pay
per-column row materialization for every job; these projections only fetch
the columns the rerun checks read, keeping multi-thousand-job selections
cheap (batch rerun preview).
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from server.app.jobs.queries.connection import ConnectionQueriesMixin
from server.app.jobs.queries.job_bulk_sql import id_chunks


class JobRerunStateQueriesMixin(ConnectionQueriesMixin):
    def list_job_rerun_states_for_jobs(
        self, workspace_id: str, job_ids: Sequence[str]
    ) -> dict[str, dict[str, Any]]:
        """Narrow job rows keyed by id for batch rerun eligibility checks.

        Only the columns the checks read (status/workspace_id/workflow_key
        plus the definition snapshot); a full ``select *`` pays per-column row
        materialization for thousands of jobs. Deliberately NOT filtered by
        workspace: the batch write path must distinguish not-found from
        foreign-workspace ids, so the workspace check happens in Python.
        """
        del workspace_id  # workspace scoping is the caller's semantic check
        by_id: dict[str, dict[str, Any]] = {}
        # #712: ≤CHUNK_ROWS ids per statement (same chunking as
        # fetch_jobs_by_ids) — no single giant IN list for large selections.
        for chunk in id_chunks(job_ids):
            sql = (
                "select id, workspace_id, status, workflow_definition_snapshot_json"
                f" from jobs where id in ({','.join('%s' for _ in chunk)})"
            )
            with self._connect_read() as conn:
                rows = conn.execute(sql, chunk).fetchall()
            by_id.update({str(row["id"]): dict(row) for row in rows})
        return by_id

    def list_job_node_states_for_jobs(
        self, job_ids: Sequence[str]
    ) -> dict[str, list[dict[str, Any]]]:
        """Narrow per-job node rows (job_id/node_key/status) for batch checks.

        Same grouping and per-job id ordering as ``list_job_nodes_for_jobs``;
        skipping the wide columns keeps large selections cheap.
        """
        grouped: dict[str, list[dict[str, Any]]] = {str(job_id): [] for job_id in job_ids}
        # Chunks partition job ids, so per-job node order (by id) is kept.
        for chunk in id_chunks(job_ids):
            placeholders = ",".join("%s" for _ in chunk)
            with self._connect_read() as conn:
                rows = conn.execute(
                    f"select job_id, node_key, status from job_nodes"
                    f" where job_id in ({placeholders}) order by job_id, id",
                    chunk,
                ).fetchall()
            for row in rows:
                grouped[str(row["job_id"])].append(dict(row))
        return grouped
