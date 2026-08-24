from __future__ import annotations

from server.app.jobs.queries.base import JobQueriesBase


class JobKeyQueriesMixin(JobQueriesBase):
    def list_job_dedup_keys(self, workspace_id: str, workflow_key: str) -> set[tuple[str, str]]:
        """Return the ``(source_type, source_id)`` dedup keys of one workflow's jobs.

        Dedup is scoped per workflow: an item already processed by workflow A
        must not be dropped as a duplicate when workflow B is asked to handle
        the same ``(source_type, source_id)``. Lightweight projection for
        intake dedup: it avoids materializing full job rows (notably the
        multi-KB ``workflow_definition_snapshot_json``) and is served by
        ``idx_jobs_workspace_workflow_source`` as a covering index scan on
        the ``(workspace_id, workflow_key)`` prefix.
        """
        with self._connect_read() as conn:
            rows = conn.execute(
                "select source_type, source_id from jobs where workspace_id=%s and workflow_key=%s",
                (workspace_id, workflow_key),
            )
            return {(str(row["source_type"]), str(row["source_id"])) for row in rows}
