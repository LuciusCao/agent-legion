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
    names: frozenset[str] | set[str],
) -> list[dict[str, Any]]:
    """按名精确删除清单行（与暂存集严格互补，同 rerun 的删除语义）。"""
    if not names:
        return []
    marks = ",".join("%s" for _ in names)
    return [
        dict(row)
        for row in conn.execute(
            f"delete from job_artifacts where job_id=%s and name in ({marks})"
            " returning node_key, name, storage_key",
            (job_id, *sorted(names)),
        ).fetchall()
    ]
