"""Job workflow upgrade 的节点重置 mutation（issue #645 双模式）。

``upgrade_job_workflow_inherit`` 是唯一的写入口（clean = inherit_nodes
为空集）；``workflow_upgrade_mutation.py`` 保留旧签名薄封装。
"""

from __future__ import annotations

from typing import Any

from server.app.db.connection import DatabaseConnection
from server.app.workflows.sharding import delete_shards


def upgrade_job_workflow_inherit(
    conn: DatabaseConnection,
    job_id: str,
    *,
    workflow_revision_id: str,
    workflow_version: int,
    workflow_definition_hash: str,
    workflow_definition_snapshot_json: str,
    node_keys: list[str],
    frozen_config_json: str | None = None,
    inherit_nodes: frozenset[str] = frozenset(),
    staged_artifact_names: frozenset[str] | set[str] = frozenset(),
) -> dict[str, Any]:
    """Re-pin a job to a revision, resetting node states per upgrade mode.

    ``inherit_nodes`` 为空集即 clean 模式：全部节点删除重建为 pending
    （既有行为——行删除重插，id/顺序与历史一致）。非空集即 inherit 模式：
    集合内**且既有状态为 completed** 的节点保留原行不动（未变子图继承产
    物——状态与时间戳原样，调度器按节点状态 + 输入文件调度，completed
    天然跳过）；其余节点（变更/新增/未完成的继承候选）删除重插 pending。
    继承候选中未完成的节点本来就没有产物可继承，重置与 clean 语义一致。

    重置节点的清理对照 ``mark_nodes_for_rerun``（#508）：``node_runs``
    目录引用清空（历史日志不指向将被覆盖的目录）、shard 行删除（下次
    tick 重新物化）；``staged_artifact_names`` 是调用方 ``stage_outputs``
    为同一重置闭包暂存的本地产物名（outputs 减 RMW）——它们的
    ``job_artifacts`` 清单行在本事务内删除，避免重跑失败时 API 仍展示
    /回填旧产物（review P1-3）。继承节点的行不在重置集里，天然保留
    （零存储改动）。

    返回 ``{"kept": …, "rerun": …, "deleted_rows": […]}``（clean 模式恒为
    全 rerun；``deleted_rows`` 携带 ``storage_key`` 供提交后 best-effort
    对象删除）。
    """
    existing_rows = _existing_node_states(conn, job_id)
    kept_nodes = {
        key
        for key in inherit_nodes
        if key in set(node_keys) and existing_rows.get(key) == "completed"
    }

    if kept_nodes:
        keep_marks = ",".join("%s" for _ in kept_nodes)
        conn.execute(
            f"delete from job_nodes where job_id=%s and node_key not in ({keep_marks})",
            (job_id, *sorted(kept_nodes)),
        )
    else:
        conn.execute("delete from job_nodes where job_id=%s", (job_id,))
    reset_nodes: list[str] = []
    for node_key in node_keys:
        if node_key in kept_nodes:
            continue
        reset_nodes.append(node_key)
        conn.execute(
            """
            insert into job_nodes(job_id, node_key, status, created_at)
            values (%s, %s, 'pending', current_timestamp)
            """,
            (job_id, node_key),
        )

    if reset_nodes:
        placeholders = ",".join("%s" for _ in reset_nodes)
        conn.execute(
            f"""
            update node_runs
            set run_dir='', session_dir=''
            where job_id=%s and node_key in ({placeholders})
            """,
            (job_id, *sorted(reset_nodes)),
        )
        delete_shards(conn, job_id, reset_nodes)
    deleted_rows = _delete_reset_artifact_rows(conn, job_id, reset_nodes, staged_artifact_names)
    conn.execute(
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
            updated_at=current_timestamp
        where id=%s
        """,
        (
            workflow_revision_id,
            workflow_version,
            workflow_definition_hash,
            workflow_definition_snapshot_json,
            frozen_config_json,
            job_id,
        ),
    )
    return {"kept": len(kept_nodes), "rerun": len(reset_nodes), "deleted_rows": deleted_rows}


def _delete_reset_artifact_rows(
    conn: DatabaseConnection,
    job_id: str,
    reset_nodes: list[str],
    staged_artifact_names: frozenset[str] | set[str],
) -> list[dict[str, Any]]:
    """删除重置节点已暂存产物的 ``job_artifacts`` 行（同一事务，#508 语义）。

    只删 ``staged_artifact_names`` 内的名字：本地文件已被调用方移进
    ``.staged`` 的产物才清清单，RMW 产物（未暂存）保留行与文件。返回删
    除行（含 ``storage_key``）供提交后对象存储清理。
    """
    if not reset_nodes or not staged_artifact_names:
        return []
    placeholders = ",".join("%s" for _ in reset_nodes)
    name_marks = ",".join("%s" for _ in staged_artifact_names)
    return [
        dict(row)
        for row in conn.execute(
            f"""
            delete from job_artifacts
            where job_id=%s and node_key in ({placeholders}) and name in ({name_marks})
            returning node_key, name, storage_key
            """,
            (job_id, *sorted(reset_nodes), *sorted(staged_artifact_names)),
        ).fetchall()
    ]


def _existing_node_states(conn: DatabaseConnection, job_id: str) -> dict[str, Any]:
    rows = conn.execute(
        "select node_key, status from job_nodes where job_id=%s", (job_id,)
    ).fetchall()
    return {str(row["node_key"]): str(row["status"]) if row["status"] else None for row in rows}
