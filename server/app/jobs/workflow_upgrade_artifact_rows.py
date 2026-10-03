"""upgrade mutation 的 ``job_artifacts`` 清单行清理（issue #645 A4）。

拆自 ``workflow_upgrade_mutation_inherit``（文件预算；与
``atomic_mutations`` 同层的数据层 SQL）：重置节点与 rename 旧 key 的
暂存名清单行删除 + 既有节点状态读取。
"""

from __future__ import annotations

from typing import Any

from server.app.db.connection import DatabaseConnection
from server.app.jobs.artifact_row_retire import retire_artifact_rows_by_name


def delete_reset_artifact_rows(
    conn: DatabaseConnection,
    job_id: str,
    reset_nodes: list[str],
    staged_artifact_names: frozenset[str] | set[str],
    renamed_from_nodes: frozenset[str] = frozenset(),
) -> list[dict[str, Any]]:
    """删除重置节点已暂存产物的 ``job_artifacts`` 行（同一事务，#508 语义）。

    只删 ``staged_artifact_names`` 内的名字：本地文件已被调用方移进
    ``.staged`` 的产物才清清单，RMW 产物（未暂存）保留行与文件。
    ``renamed_from_nodes`` 是新 revision 中已不存在的旧 key（rename a→a2
    后旧 key 的行匹配不到按新 key 构建的 reset 集）：新节点同名 outputs 的
    暂存即证明该名字将被重跑覆盖，旧 key 的同名行一并删除；没有 outputs
    声明的节点无从按名字暂存，行留给对象存储生命周期兜底（A4 保守子集）。
    #827 起删除按名（不再按 node_key 过滤）：两个节点集只决定「本次有无
    退役面」，同名的全部行与对象同生共死。
    返回删除行（含 ``storage_key``）供提交后对象存储清理。
    """
    if not (set(reset_nodes) | set(renamed_from_nodes)) or not staged_artifact_names:
        return []
    # #827：按名退役——对象槽按名字寻址，只删重置面 node_key 的行会让
    # 同名遗留行（被删节点、保留声明面挡下的旧生产者）存活并指向被删
    # 写者的字节（hash 不符 / 换形后 NoSuchKey，hydration 永久 defer）。
    # 暂存名集合已排除保留节点声明面（A3 / keep_io / rmw_retire），按名
    # 删除不会触碰继承节点的有效产物。
    return retire_artifact_rows_by_name(conn, job_id, staged_artifact_names)


def delete_all_artifact_rows(
    conn: DatabaseConnection, job_id: str, keep_input_names: frozenset[str] | set[str]
) -> list[dict[str, Any]]:
    """删除该 job 的全部 ``job_artifacts`` 清单行，除受保护输入名（codex 五轮 P2-D）。

    clean 语义分支（无任何继承节点——显式 clean 模式或 inherit 的保守
    退化：旧快照损坏 / NULL frozen 不可证明）专用：全部节点重置 pending、
    全部产物作废。旧快照不可解析时 ``removed_artifact_face`` 只能返回
    空面，按名字暂存的删除路径匹配不到旧节点 key / 改名输出的行——残留
    行会让产物 API 继续展示旧 revision 产物、对象存储权威引用悬挂。清空
    全部行是退化 clean 语义的应有之义（与 A1/S7 的「退化 = 全量重跑 =
    旧产物作废」对齐）。

    ``keep_input_names``（#759 复审 P1-A 起 = 保护计划 keep 集）例外：
    这些名字的旧字节即权威（外部输入 / RMW 启动名 / 保留节点声明面），
    删行会让 ``restore_missing_inputs`` 无清单可回、节点永久等输入——
    与 rerun 保留 RMW 输入的 #114 语义一致。返回删除行供提交后对象清理。
    """
    if keep_input_names:
        name_marks = ",".join("%s" for _ in keep_input_names)
        sql = (
            f"delete from job_artifacts where job_id=%s"
            f" and name not in ({name_marks}) returning node_key, name, storage_key"
        )
        params: tuple = (job_id, *sorted(keep_input_names))
    else:
        sql = "delete from job_artifacts where job_id=%s returning node_key, name, storage_key"
        params = (job_id,)
    return [dict(row) for row in conn.execute(sql, params).fetchall()]


def existing_node_states(conn: DatabaseConnection, job_id: str) -> dict[str, Any]:
    """job_nodes 现有行 → {node_key: status}（升级事务内读取）。"""
    rows = conn.execute(
        "select node_key, status from job_nodes where job_id=%s", (job_id,)
    ).fetchall()
    return {str(row["node_key"]): str(row["status"]) if row["status"] else None for row in rows}
