"""#1279：材料 object_etag 身份的建立/重绑/回填查询面（queries 层）。

三个模块级函数（ConnectSource 驱动，facade 薄壳在 material_repair.py /
material_claim_gate.py  delegate；BOUNDARY-DATA-001：service 与脚本侧经
ConnectSource 直通，不手写 SQL）：

- ``bind_material_etag_if_current``——CAS 落 object_etag：仅当行的
  （object_etag, storage_key）仍等于期望值时落新值。key 参与 CAS
  （codex r8）：repair 会清 etag 并轮换 key——只按 etag CAS 会把旧 key
  的 HEAD etag 绑到指向新 key 的行上。消费方：恢复重绑脚本（rebind）
  与启动回填（backfill）。
- ``materials_with_object_etag``——恢复重绑扫描面（运维低频全量扫描，
  无索引可接受）。
- ``legacy_etag_backfill_candidates``——启动回填候选：object_etag=''
  的 ready 行（uploading 行在 complete 时重新核验落 etag，expired 行
  复活走同一路径，都不需要回填）。
"""

from __future__ import annotations

from typing import Any

from server.app.db.dialect import ConnectSource
from server.app.db.transaction import read_connection, write_transaction


def bind_material_etag_if_current(
    connect_source: ConnectSource,
    material_id: str,
    *,
    expected_etag: str,
    new_etag: str,
    storage_key: str,
) -> bool:
    """CAS 落 object_etag（见模块 docstring）；返回是否落库。"""
    with write_transaction(connect_source) as conn:
        result = conn.execute(
            "update materials set object_etag=%s where id=%s and object_etag=%s and storage_key=%s",
            (new_etag, material_id, expected_etag, storage_key),
        )
    return result.rowcount > 0


def materials_with_object_etag(connect_source: ConnectSource) -> list[dict[str, Any]]:
    """恢复重绑扫描面：记录了 object_etag 的全部材料行（跨 workspace）。"""
    with read_connection(connect_source) as conn:
        rows = conn.execute(
            "select id, workspace_id, content_hash, size_bytes, storage_key,"
            " object_etag from materials where object_etag <> ''"
        ).fetchall()
    return [dict(row) for row in rows]


def legacy_etag_backfill_candidates(
    connect_source: ConnectSource, limit: int
) -> list[dict[str, Any]]:
    """启动回填候选（codex r8）：object_etag='' 的 ready 行。"""
    with read_connection(connect_source) as conn:
        rows = conn.execute(
            "select id, storage_key from materials"
            " where status='ready' and object_etag = ''"
            " order by created_at, id limit %s",
            (limit,),
        ).fetchall()
    return [dict(row) for row in rows]
