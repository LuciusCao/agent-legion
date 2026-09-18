"""Job workflow upgrade 的节点重置 mutation（issue #645 双模式）。

``upgrade_job_workflow_inherit`` 是唯一的写入口（clean = inherit_nodes
为空集）；``workflow_upgrade_mutation.py`` 保留旧签名薄封装，
``workflow_upgrade_artifact_rows.py`` 承载清单行清理 SQL（文件预算拆分）。
"""

from __future__ import annotations

from typing import Any

from server.app.db.connection import DatabaseConnection
from server.app.jobs.atomic_mutations import _cancel_queued_sql
from server.app.jobs.workflow_upgrade_artifact_rows import (
    delete_all_artifact_rows,
    delete_reset_artifact_rows,
    existing_node_states,
)
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
    keep_input_names: frozenset[str] | set[str] = frozenset(),
    full_manifest_cleanup: bool = False,
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
    tick 重新物化）、queued agent 请求取消（旧 revision 的 manifest 不
    允许在新 revision 作业上抢跑）；``staged_artifact_names`` 是调用方
    ``stage_outputs`` 为同一重置闭包暂存的本地产物名（outputs 减 RMW）
    ——它们的 ``job_artifacts`` 清单行在本事务内删除，避免重跑失败时
    API 仍展示/回填旧产物（review P1-3）；新 revision 中已消失的旧节点
    key（rename 前身份）的同名行一并删除（A4）。继承节点的行不在重置
    集里，天然保留（零存储改动）。**无任何继承节点时**（显式 clean 或
    inherit 保守退化：旧快照损坏 / NULL frozen 不可证明）全部清单行
    清空（codex 五轮 P2-D）——退化 clean 的语义是旧产物全部作废，
    按名字暂存的删除匹配不到旧 key / 改名输出的行。

    返回 ``{"kept": …, "rerun": …, "deleted_rows": […]}``（clean 模式恒为
    全 rerun；``deleted_rows`` 携带 ``storage_key`` 供提交后 best-effort
    对象删除）。
    """
    existing_rows = existing_node_states(conn, job_id)
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
        # A2：旧 revision 入队的 queued agent 请求必须取消（manifest 携带
        # 旧语义，claim 复查链在新 pending 行上放行会抢跑），与
        # mark_nodes_for_rerun 同款；clean 模式自 base 起同样缺失，一并补上。
        conn.execute(_cancel_queued_sql(placeholders), (job_id, *sorted(reset_nodes)))
    # A4：新 revision 已消失的旧节点 key（rename 前身份）的同名清单行一并
    # 清理（行匹配不到按新 key 构建的 reset 集，不删就是永久孤儿行）。
    renamed_from_nodes = frozenset(existing_rows) - frozenset(node_keys)
    if kept_nodes or not full_manifest_cleanup:
        # 继承分支按名删除（继承节点的行不在重置面，天然保留）；裸构造
        # 服务（未装配暂存）同样走既有按名删除——full_manifest_cleanup
        # 默认 False，直连 mutation 的调用面行为不变。
        deleted_rows = delete_reset_artifact_rows(
            conn, job_id, reset_nodes, staged_artifact_names, renamed_from_nodes
        )
    else:
        # codex 五轮 P2-D：无任何继承节点（显式 clean 或 inherit 保守退化）
        # = 全部产物作废——清空全部清单行（除新图声明的输入名：RMW/外部
        # 输入是重置节点的启动输入，#114 语义），覆盖按名字暂存匹配不到
        # 的旧 key / 改名输出 / 损坏快照侧的残留面。
        deleted_rows = delete_all_artifact_rows(conn, job_id, frozenset(keep_input_names))
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
