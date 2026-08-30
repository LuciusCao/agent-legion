from __future__ import annotations

from server.app.jobs.queries.connection import ConnectionQueriesMixin


class JobKeyQueriesMixin(ConnectionQueriesMixin):
    def list_job_dedup_keys(self, workspace_id: str, workflow_key: str) -> set[tuple[str, str]]:
        """Return the ``(source_type, source_id)`` dedup keys of one workspace's jobs.

        Dedup is scoped per workspace: an item already processed by workflow A
        must not be dropped as a duplicate when workflow B is asked to handle
        the same ``(source_type, source_id)``. #211 Phase 3 (read-layer
        binding): the predicate keys on workspace_id alone — workflow_key
        equals it on every row (v62), so the column filter was redundant and
        the signature parameter is kept for callers only. The workspace
        prefix of idx_jobs_workspace_workflow_source still narrows the scan
        (the source columns sit behind the workflow_key column until Phase 4
        drops it, so the projection reads the heap for those columns).
        """
        with self._connect_read() as conn:
            rows = conn.execute(
                "select source_type, source_id from jobs where workspace_id=%s",
                (workspace_id,),
            )
            return {(str(row["source_type"]), str(row["source_id"])) for row in rows}
