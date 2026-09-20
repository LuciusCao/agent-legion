from __future__ import annotations

from server.app.db.connection import DatabaseConnection


def upgrade_job_workflow(
    conn: DatabaseConnection,
    job_id: str,
    *,
    workflow_revision_id: str,
    workflow_version: int,
    workflow_definition_hash: str,
    workflow_definition_snapshot_json: str,
    node_keys: list[str],
    frozen_config_json: str | None = None,
) -> None:
    # EXEC-GENERATION-001：clean 升级是重置类突变，bump 恰好一次并 fold 进
    # 自身的 jobs UPDATE（returning 新代次），重建的 job_nodes 行盖同一戳。
    # bump 先于节点行重建，保证盖戳用的是新代次。
    row = conn.execute(
        """
        update jobs
        set status='queued',
            error_message='',
            workflow_revision_id=%s,
            workflow_version=%s,
            workflow_definition_hash=%s,
            workflow_definition_snapshot_json=%s,
            frozen_config_json=%s,
            execution_mode='full',
            target_node_key=null,
            execution_paused=0,
            pause_reason='',
            updated_at=current_timestamp,
            execution_generation=execution_generation+1
        where id=%s
        returning execution_generation
        """,
        (
            workflow_revision_id,
            workflow_version,
            workflow_definition_hash,
            workflow_definition_snapshot_json,
            frozen_config_json,
            job_id,
        ),
    ).fetchone()
    if row is None:
        raise ValueError(f"Unknown job: {job_id}")
    generation = row["execution_generation"]
    conn.execute("delete from job_nodes where job_id=%s", (job_id,))
    for node_key in node_keys:
        conn.execute(
            """
            insert into job_nodes(job_id, node_key, status, created_at, execution_generation)
            values (%s, %s, 'pending', current_timestamp, %s)
            """,
            (job_id, node_key, generation),
        )
