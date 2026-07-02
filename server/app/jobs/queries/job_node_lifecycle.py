from __future__ import annotations

import sqlite3

from server.app.jobs.queries.base import JobQueriesBase
from server.app.workflows.definition import WorkflowDefinition


class JobNodeLifecycleQueriesMixin(JobQueriesBase):
    def mark_nodes_not_applicable(self, job_id: str, node_keys: list[str], reason: str) -> None:
        if not node_keys:
            return
        placeholders = ",".join("?" for _ in node_keys)
        with self.connect() as conn:
            conn.execute(
                f"""
                update job_nodes
                set status='not_applicable',
                    stale_reason=?,
                    error_message='',
                    finished_at=current_timestamp
                where job_id=? and node_key in ({placeholders})
                  and status in ('pending', 'ready', 'stale')
                """,
                [reason, job_id, *node_keys],
            )

    def mark_node_for_rerun(self, job_id: str, node_key: str, downstream: list[str]) -> None:
        with self.connect() as conn:
            cursor = conn.execute(
                """
                update job_nodes
                set status='pending',
                    stale_reason='',
                    error_message='',
                    started_at=null,
                    finished_at=null,
                    created_at=current_timestamp
                where job_id=? and node_key=?
                """,
                (job_id, node_key),
            )
            if cursor.rowcount == 0:
                raise ValueError(f"Unknown job node: {job_id}.{node_key}")
            for downstream_key in downstream:
                conn.execute(
                    """
                    update job_nodes
                    set status='stale',
                        stale_reason=?,
                        error_message='',
                        created_at=current_timestamp
                    where job_id=? and node_key=?
                    """,
                    (f"upstream {node_key} rerun", job_id, downstream_key),
                )
            conn.execute(
                """
                update jobs
                set status='queued',
                    error_message='',
                    packed=0,
                    updated_at=current_timestamp
                where id=?
                """,
                (job_id,),
            )

    def _sync_job_status_after_node_run(
        self,
        conn: sqlite3.Connection,
        run: sqlite3.Row,
        status: str,
        definition: WorkflowDefinition | None,
    ) -> None:
        if definition is not None:
            node = definition.nodes.get(str(run["node_key"]))
            if node is not None and node.terminal is not None and status == "completed":
                conn.execute(
                    "update jobs set outcome=?, updated_at=current_timestamp where id=?",
                    (node.terminal.outcome, run["job_id"]),
                )
        still_running = conn.execute(
            "select 1 from job_nodes where job_id=? and status='running'",
            (run["job_id"],),
        ).fetchone()
        if still_running is None:
            any_failed = conn.execute(
                "select 1 from job_nodes where job_id=? and status='failed'",
                (run["job_id"],),
            ).fetchone()
            if any_failed is not None:
                new_status = "failed"
            else:
                all_terminal_success = conn.execute(
                    """
                    select 1 from job_nodes
                    where job_id=? and status not in ('completed', 'not_applicable')
                    """,
                    (run["job_id"],),
                ).fetchone()
                new_status = "completed" if all_terminal_success is None else "queued"
            conn.execute(
                "update jobs set status=?, updated_at=current_timestamp where id=?",
                (new_status, run["job_id"]),
            )
