from __future__ import annotations

import sqlite3


def upgrade_job_workflow(
    conn: sqlite3.Connection,
    job_id: str,
    *,
    workflow_revision_id: str,
    workflow_version: int,
    workflow_definition_hash: str,
    workflow_definition_snapshot_json: str,
    node_keys: list[str],
) -> None:
    conn.execute("delete from job_nodes where job_id=?", (job_id,))
    for node_key in node_keys:
        conn.execute(
            """
            insert into job_nodes(job_id, node_key, status, created_at)
            values (?, ?, 'pending', current_timestamp)
            """,
            (job_id, node_key),
        )
    conn.execute(
        """
        update jobs
        set status='queued',
            error_message='',
            workflow_revision_id=?,
            workflow_version=?,
            workflow_definition_hash=?,
            workflow_definition_snapshot_json=?,
            execution_mode='full',
            target_node_key=null,
            execution_paused=0,
            pause_reason='',
            updated_at=current_timestamp
        where id=?
        """,
        (
            workflow_revision_id,
            workflow_version,
            workflow_definition_hash,
            workflow_definition_snapshot_json,
            job_id,
        ),
    )
