from __future__ import annotations

from server.app.jobs.queries.base import JobQueriesBase


class JobStatusQueriesMixin(JobQueriesBase):
    def count_jobs_by_status(self, workspace_id: str) -> dict[str, int]:
        with self._connect_read() as conn:
            rows = conn.execute(
                "select status, count(*) as cnt from jobs where workspace_id = %s group by status",
                (workspace_id,),
            )
            result: dict[str, int] = {}
            for row in rows:
                status = row["status"]
                if status == "queued":
                    status = "pending"
                result[status] = result.get(status, 0) + row["cnt"]
            return result

    def count_workspace_job_nodes_by_status(
        self, workspace_id: str, workflow_key: str
    ) -> dict[str, dict[str, int]]:
        result: dict[str, dict[str, int]] = {}
        with self._connect_read() as conn:
            rows = conn.execute(
                """
                select job_nodes.node_key, job_nodes.status, count(*) as cnt
                from job_nodes
                join jobs on jobs.id = job_nodes.job_id
                where jobs.workspace_id = %s and jobs.workflow_key = %s
                group by job_nodes.node_key, job_nodes.status
                """,
                (workspace_id, workflow_key),
            )
            for row in rows:
                node_counts = result.setdefault(row["node_key"], {})
                node_counts[row["status"]] = row["cnt"]
        return result
