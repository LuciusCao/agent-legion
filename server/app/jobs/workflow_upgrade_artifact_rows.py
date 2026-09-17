"""upgrade mutation 的 ``job_artifacts`` 清单行清理（issue #645 A4）。

拆自 ``workflow_upgrade_mutation_inherit``（文件预算；与
``atomic_mutations`` 同层的数据层 SQL）：重置节点与 rename 旧 key 的
暂存名清单行删除 + 既有节点状态读取。
"""

from __future__ import annotations

from typing import Any

from server.app.db.connection import DatabaseConnection


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
    返回删除行（含 ``storage_key``）供提交后对象存储清理。
    """
    node_set = sorted(set(reset_nodes) | set(renamed_from_nodes))
    if not node_set or not staged_artifact_names:
        return []
    node_marks = ",".join("%s" for _ in node_set)
    name_marks = ",".join("%s" for _ in staged_artifact_names)
    return [
        dict(row)
        for row in conn.execute(
            f"""
            delete from job_artifacts
            where job_id=%s and node_key in ({node_marks}) and name in ({name_marks})
            returning node_key, name, storage_key
            """,
            (job_id, *node_set, *sorted(staged_artifact_names)),
        ).fetchall()
    ]


def existing_node_states(conn: DatabaseConnection, job_id: str) -> dict[str, Any]:
    """job_nodes 现有行 → {node_key: status}（升级事务内读取）。"""
    rows = conn.execute(
        "select node_key, status from job_nodes where job_id=%s", (job_id,)
    ).fetchall()
    return {str(row["node_key"]): str(row["status"]) if row["status"] else None for row in rows}
