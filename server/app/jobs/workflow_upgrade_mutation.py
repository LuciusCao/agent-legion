from __future__ import annotations

from server.app.agent_broker.manifest_trim import cancel_queued_requests_for_job
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
    # 节点集合整体重建前了结全部 queued 请求（含已不在新定义里的旧节点）：
    # 能认领旧 payload 的 Worker 离线时，遗留 queued 行不会触发任何代次
    # CAS 清理，却一直被 has_active_request 视为 active，把新 revision 的
    # 重派无限期挡住（#759 review P1）。与 rerun 的 _cancel_queued_sql
    # 同语义，只是作用域为整个 job。
    cancel_queued_requests_for_job(conn, job_id)
    conn.execute("delete from job_nodes where job_id=%s", (job_id,))
    for node_key in node_keys:
        conn.execute(
            """
            insert into job_nodes(job_id, node_key, status, created_at, execution_generation)
            values (%s, %s, 'pending', current_timestamp, %s)
            """,
            (job_id, node_key, generation),
        )
