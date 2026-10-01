from __future__ import annotations

import logging
from typing import Any

from server.app.db.connection import DatabaseConnection
from server.app.jobs.queries.connection import ConnectionQueriesMixin
from server.app.workflows.definition import WorkflowDefinition

logger = logging.getLogger(__name__)


class JobNodeLifecycleQueriesMixin(ConnectionQueriesMixin):
    def mark_nodes_not_applicable_many(
        self, entries: list[tuple[str, list[str], str, int]]
    ) -> None:
        """Batch mark nodes not applicable across many jobs in one connection.

        EXEC-GENERATION-001: each entry carries the epoch the evaluation read
        (the scan's fat job row). The write re-reads the epoch under the
        ``job-mutation:<job_id>`` advisory lock — the same lock every reset
        mutation holds while it bumps the epoch and rebuilds node rows — and
        skips the entry on mismatch: flipping new-epoch pending rows from a
        stale branch verdict could park them at ``not_applicable`` forever
        (job_nodes are not part of the scan mark and this write does not bump
        ``jobs.updated_at``, so the evaluation cache would never invalidate).
        A skipped entry re-evaluates on the next poll pass (the epoch bump
        changed the scan mark).
        """
        if not entries:
            return
        from server.app.executors._lease_control import lock_job_mutation_and_read_generation

        with self.connect() as conn:
            # The batch is single-workspace by construction (the scan fetches
            # fat rows for one workspace per pass), so plain job_id order
            # coincides with the global (ws lock key, job_id) batch order of
            # EXEC-GENERATION-001.
            for job_id, node_keys, reason, expected_generation in sorted(entries):
                if not node_keys:
                    continue
                current_generation = lock_job_mutation_and_read_generation(conn, job_id)
                if current_generation != expected_generation:
                    logger.warning(
                        "skipping stale not_applicable mark for job %s: "
                        "generation %s evaluated, %s current",
                        job_id,
                        expected_generation,
                        current_generation,
                    )
                    continue
                placeholders = ",".join("%s" for _ in node_keys)
                conn.execute(
                    f"""
                    update job_nodes
                    set status='not_applicable',
                        stale_reason=%s,
                        error_message='',
                        finished_at=current_timestamp
                    where job_id=%s and node_key in ({placeholders})
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
                where job_id=%s and node_key=%s
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
                        stale_reason=%s,
                        error_message='',
                        created_at=current_timestamp
                    where job_id=%s and node_key=%s
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
                where id=%s
                """,
                (job_id,),
            )

    def _sync_job_status_after_node_run(
        self,
        conn: DatabaseConnection,
        run: dict[str, Any],
        status: str,
        definition: WorkflowDefinition | None,
    ) -> None:
        if definition is not None:
            node = definition.nodes.get(str(run["node_key"]))
            if node is not None and node.terminal is not None and status == "completed":
                conn.execute(
                    "update jobs set outcome=%s, updated_at=current_timestamp where id=%s",
                    (node.terminal.outcome, run["job_id"]),
                )
        still_running = conn.execute(
            "select 1 from job_nodes where job_id=%s and status='running'",
            (run["job_id"],),
        ).fetchone()
        if still_running is None:
            any_failed = conn.execute(
                "select 1 from job_nodes where job_id=%s and status='failed'",
                (run["job_id"],),
            ).fetchone()
            if any_failed is not None:
                new_status = "failed"
            else:
                all_terminal_success = conn.execute(
                    """
                    select 1 from job_nodes
                    where job_id=%s and status not in ('completed', 'not_applicable')
                    """,
                    (run["job_id"],),
                ).fetchone()
                new_status = "completed" if all_terminal_success is None else "queued"
            conn.execute(
                "update jobs set status=%s, updated_at=current_timestamp where id=%s",
                (new_status, run["job_id"]),
            )
