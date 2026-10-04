"""退役产物清单行的按名删除（#827：清单行与权威对象同生共死）。

权威对象键 ``jobs/<ws>/<job>/<name>``（``artifact_storage_key``）只按名字
寻址，``job_artifacts`` 却按 ``(job_id, node_key, name)`` 一节点一行——同名
多行共享同一个物理对象槽，槽里只有最后一个写者的字节。rerun / run-to /
upgrade inherit 退役某个名字时若只删 ``node_key ∈ 重置面`` 的行，同名但
node_key 在重置面之外的行（被删节点的遗留行、保留节点声明面挡下的旧
生产者行等）会存活：提交后的对象清理把它们当作「仍被引用」跳过删除，
被删的又恰是最新写者时，幸存旧行成为 hydration / 产物 API 的「最新」
行，对象里却是被删写者的字节——content-hash 不符或对象换形后缺失，
ready-gate hydration 永远恢复不全（#827 永久 defer）。

按名退役是安全的：三条调用路径的退役名集合（``staging_output_names`` 的
A3 排除、upgrade 的 removed 面 keep_io 过滤、``rmw_retire`` 的保留声明
面排除）都已保证「重置面之外没有任何节点在当前定义里声明该名为输出」
——重置面外的同名行不是任何现存节点的有效产物，只是对象槽的悬挂别名。
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from server.app.db.connection import DatabaseConnection


def retire_artifact_rows_by_name(
    conn: DatabaseConnection, job_id: str, names: Iterable[str]
) -> list[dict[str, Any]]:
    """删除该 job 下 ``names`` 的全部清单行（不分 node_key），返回删除行。

    返回行携带 ``storage_key`` 供提交后对象清理——同名的 raw / ``.gz``
    两种形态都在其中，对象删除走查对每个 key 复核存活后删除。
    """
    retired = sorted(set(names))
    if not retired:
        return []
    marks = ",".join("%s" for _ in retired)
    return [
        dict(row)
        for row in conn.execute(
            f"""
            delete from job_artifacts
            where job_id=%s and name in ({marks})
            returning node_key, name, storage_key
            """,
            (job_id, *retired),
        ).fetchall()
    ]
