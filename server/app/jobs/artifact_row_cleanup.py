"""job_artifacts 清单行的事务内删除（#759 预算拆分）。

与 ``job_artifact_rows.upsert_artifact_row_tx`` 对称的删除臂：返回被删行
（含 ``storage_key``），供调用方在提交后做对象存储的 best-effort 删除
（同 mark_nodes_for_rerun / upgrade_job_workflow 的约定）。
"""

from __future__ import annotations

from typing import Any

from server.app.db.connection import DatabaseConnection


def delete_job_artifact_rows_tx(
    conn: DatabaseConnection,
    job_id: str,
    *,
    preserve_names: frozenset[str] | set[str] = frozenset(),
) -> list[dict[str, Any]]:
    """删除一个 job 的全部清单行（``preserve_names`` 豁免，如 RMW 名）。"""
    if preserve_names:
        return [
            dict(row)
            for row in conn.execute(
                "delete from job_artifacts where job_id=%s and not (name = any(%s))"
                " returning node_key, name, storage_key",
                (job_id, sorted(preserve_names)),
            ).fetchall()
        ]
    return [
        dict(row)
        for row in conn.execute(
            "delete from job_artifacts where job_id=%s returning node_key, name, storage_key",
            (job_id,),
        ).fetchall()
    ]
