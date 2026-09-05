from __future__ import annotations

from server.app.jobs.queries.connection import ConnectionQueriesMixin


class JobKeyQueriesMixin(ConnectionQueriesMixin):
    def list_job_dedup_keys(self, workspace_id: str, workflow_key: str) -> set[tuple[str, str]]:
        """All dedup keys of one workspace (deprecated full-scan shape).

        Workspace-scoped ``(source_type, source_id)``, index-only on
        idx_jobs_workspace_source; workflow_key is identity-only since v62
        (#211). Retained for tests pinning the full-set contract — run
        creation uses the chunked point lookups in run_item_probes instead
        (#467 A2: full scans scale with workspace size, not the submission).
        """
        with self._connect_read() as conn:
            rows = conn.execute(
                "select source_type, source_id from jobs where workspace_id=%s",
                (workspace_id,),
            )
            return {(str(row["source_type"]), str(row["source_id"])) for row in rows}
