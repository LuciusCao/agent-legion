"""登记事务内的被取代 key 探针（#853，设计见
docs/architecture/artifact-direct-url-pinning.md）。

#853 起每次写入落一次性版本 key，同名产物再次登记只把清单行改指新 key；
行此前指向的旧 key 由本探针在登记事务内、upsert 之前取出，登记提交后由
``services.job_artifact_versions.discard_superseded_objects`` 复核删除。
与清单行 upsert SQL 同居执行层（BOUNDARY-DATA-001：services 不手写 SQL）。
"""

from __future__ import annotations

from typing import Any

# ``for update``：并发的同行登记排队，后到者读到先提交者的 key 而不是更早
# 的快照（否则先提交者的 key 会在无行引用的情况下逃过删除）。首次登记
# （无行）无行可锁，并发首登记的落败 key 成孤儿交 GC 兜底。
SUPERSEDED_KEY_SQL = (
    "select storage_key from job_artifacts where job_id=%s and node_key=%s and name=%s for update"
)


def collect_superseded_tx(
    conn: Any, rows: list[dict[str, Any]], superseded: list[str] | None
) -> None:
    """对即将 upsert 的每一行（含 job_id/node_key/name/storage_key），把同
    (job, node, name) 行当前指向的另一个 key 追加进 ``superseded``；无行或
    同 key 不追加。``superseded`` 为 None 时不探测（调用方不清理）。"""
    if superseded is None:
        return
    for row in rows:
        current = conn.execute(
            SUPERSEDED_KEY_SQL, (str(row["job_id"]), str(row["node_key"]), str(row["name"]))
        ).fetchone()
        if current is not None and str(current["storage_key"]) != str(row["storage_key"]):
            superseded.append(str(current["storage_key"]))
