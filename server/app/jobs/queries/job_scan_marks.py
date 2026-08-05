"""Lightweight job-scan queries for the workflow worker's dirty tracking.

The worker's poll pass must not move the full ``jobs`` rows (notably the
multi-KB ``workflow_definition_snapshot_json``) for thousands of unchanged
jobs on every tick. These queries let it diff cheap marks against its
per-job evaluation cache and only fetch fat rows for jobs that changed.
"""

from __future__ import annotations

from typing import Any

from server.app.jobs.queries.base import JobQueriesBase

_ACTIVE_MARK_COLUMNS = (
    "id, workspace_id, source_id, status, execution_paused, execution_mode,"
    " target_node_key, workflow_definition_hash, created_at, updated_at"
)


class JobScanMarksMixin(JobQueriesBase):
    def list_active_job_marks(self, workflow_key: str) -> list[dict[str, Any]]:
        """Lightweight rows for every non-terminal job of a workflow."""
        with self._connect_read() as conn:
            rows = conn.execute(
                f"select {_ACTIVE_MARK_COLUMNS} from jobs"
                " where workflow_key=%s and status not in ('completed','failed')"
                " order by created_at desc",
                (workflow_key,),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_workflow_snapshot_for_hash(self, definition_hash: str) -> str:
        """Any stored snapshot for a definition hash, for lazy cache fills."""
        with self._connect_read() as conn:
            row = conn.execute(
                "select workflow_definition_snapshot_json from jobs"
                " where workflow_definition_hash=%s"
                " and workflow_definition_snapshot_json != '' limit 1",
                (definition_hash,),
            ).fetchone()
        return str(row["workflow_definition_snapshot_json"]) if row else ""
