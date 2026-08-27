from __future__ import annotations

from server.app.jobs.queries.connection import ConnectionQueriesMixin


class JobStatusQueriesMixin(ConnectionQueriesMixin):
    def count_jobs_by_status(self, workspace_id: str) -> dict[str, int]:
        # Reads the trigger-maintained counter table (DB-JOB-STATUS-COUNTS-001)
        # instead of a group-by over the workspace's whole jobs slice.
        with self._connect_read() as conn:
            rows = conn.execute(
                "select status, cnt from workspace_job_status_counts"
                " where workspace_id = %s and cnt <> 0",
                (workspace_id,),
            )
            result: dict[str, int] = {}
            for row in rows:
                status = row["status"]
                if status == "queued":
                    status = "pending"
                result[status] = result.get(status, 0) + int(row["cnt"])
            return result

    def count_workspace_job_nodes_by_status(
        self, workspace_id: str, workflow_key: str
    ) -> dict[str, dict[str, int]]:
        # Reads the trigger-maintained counter table
        # (DB-JOB-NODE-STATUS-COUNTS-001) instead of a join+group-by over the
        # workspace's whole job_nodes ⋈ jobs slice (48s at 260k jobs / 2.9M
        # job_nodes, hash join spilling ~1GB to temp — issue #121).
        result: dict[str, dict[str, int]] = {}
        with self._connect_read() as conn:
            rows = conn.execute(
                """
                select node_key, status, cnt from workspace_job_node_status_counts
                where workspace_id = %s and workflow_key = %s and cnt <> 0
                """,
                (workspace_id, workflow_key),
            )
            for row in rows:
                node_counts = result.setdefault(row["node_key"], {})
                node_counts[row["status"]] = int(row["cnt"])
        return result
