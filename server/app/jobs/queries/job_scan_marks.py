"""Lightweight job-scan queries for the workflow worker's dirty tracking.

The worker's poll pass must not move the full ``jobs`` rows (notably the
multi-KB ``workflow_definition_snapshot_json``) for thousands of unchanged
jobs on every tick. These queries let it diff cheap marks against its
per-job evaluation cache and only fetch fat rows for jobs that changed.
"""

from __future__ import annotations

from typing import Any

from server.app.jobs.queries.connection import ConnectionQueriesMixin

_ACTIVE_MARK_COLUMNS = (
    "id, workspace_id, source_id, status, execution_paused, execution_mode,"
    " target_node_key, workflow_definition_hash, execution_generation, created_at, updated_at"
)

# The predicate must stay in sync with the idx_jobs_workspace_active_marks
# partial index or every rescan seq-scans the jobs table. Kept as a module
# constant so tests can pin the exact plan of the string that production
# runs (same pattern as agent_stock_snapshot.TIER_ROWS_SQL).
# #211 Phase 3 (read-layer binding): keyed on workspace_id — workflow_key
# equals it on every row (v62), and per-workspace scans replace the legacy
# shared-key cross-workspace sweep.
ACTIVE_MARKS_SQL = (
    f"select {_ACTIVE_MARK_COLUMNS} from jobs"
    " where workspace_id=%s and status not in ('completed','failed')"
    " order by created_at desc"
)


class JobScanMarksMixin(ConnectionQueriesMixin):
    def list_active_job_marks(self, workspace_id: str) -> list[dict[str, Any]]:
        """Lightweight rows for every non-terminal job of a workspace."""
        with self._connect_read() as conn:
            rows = conn.execute(ACTIVE_MARKS_SQL, (workspace_id,)).fetchall()
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

    def get_job_execution_generation(self, job_id: str) -> int | None:
        """The job's live execution epoch (EXEC-GENERATION-001); None if gone.

        Ready-gate hydration (#759) brackets its manifest read + local
        re-materialization with two reads of this value: every reset mutation
        (rerun / upgrade / run-to / approval rework) bumps the epoch in the
        same ``lease_guarded_mutation`` transaction that deletes the reset
        outputs' manifest rows, so an unchanged epoch proves the restored
        bytes were not invalidated mid-flight.
        """
        with self._connect_read() as conn:
            row = conn.execute(
                "select execution_generation from jobs where id=%s", (job_id,)
            ).fetchone()
        return int(row["execution_generation"]) if row is not None else None
