from __future__ import annotations

from typing import Any

from server.app.agent_broker.manifest_trim import cancel_queued_requests_for_job
from server.app.db.connection import DatabaseConnection
from server.app.jobs.artifact_row_cleanup import delete_job_artifact_rows_tx


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
    preserve_artifact_names: frozenset[str] | set[str] = frozenset(),
) -> list[dict[str, Any]]:
    """重置类突变：bump 代次、了结 queued 请求、删产物清单行并重建节点。

    ``preserve_artifact_names``（RMW 名，#114/#759）的清单行与对象保留——
    rerun/run-to 入口对 RMW 三者全保留，升级不能让 RMW 种子只剩本地单
    副本。返回被删的 ``job_artifacts`` 行（含 ``storage_key``），供调用方
    在提交后做对象存储的 best-effort 删除（同 mark_nodes_for_rerun 的约定）。
    """
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
    # clean 升级全量重跑：旧 revision 的全部产物（含新定义里已删除节点的）
    # 一律失效，清单行在同事务删除——否则全节点 pending 期间作业仍在从
    # 对象存储提供上一轮产物，且隐式消费者会被旧输入文件立即解锁（#759）。
    # RMW 名豁免（保留清单行与对象，与 rerun/run-to 的 RMW 全保留对齐）。
    deleted_rows = delete_job_artifact_rows_tx(conn, job_id, preserve_names=preserve_artifact_names)
    # 分片行只 FK 到 jobs（不随 job_nodes 级联）：节点集合整体替换时必须
    # 按 job 作用域删除——否则新 revision 同名分片节点的物化被旧行跳过，
    # 沿用上一轮 input_json/状态（#759 自审 P1；rerun/run-to 都删）。
    conn.execute("delete from node_shards where job_id=%s", (job_id,))
    conn.execute("delete from job_nodes where job_id=%s", (job_id,))
    for node_key in node_keys:
        conn.execute(
            """
            insert into job_nodes(job_id, node_key, status, created_at, execution_generation)
            values (%s, %s, 'pending', current_timestamp, %s)
            """,
            (job_id, node_key, generation),
        )
    return deleted_rows
