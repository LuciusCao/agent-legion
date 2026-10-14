"""#1316：存量无 hash 材料行的 ETag 身份绑定（queries 层）。

候选 = ``status='ready' ∧ content_hash='' ∧ object_etag=''``：v98 之前完成、
没有内容身份的行。v98 之后所有产线（complete / inline / demo seed）都在落
ready 时原子记录 ETag 且空 ETag fail-closed，所以候选只可能是存量行（或
DB 恢复带回来的存量行）。hash 行不在候选里：它们由 sha256 兜底，不需要
etag（#1316 P4）。

绑定 CAS 钉住扫描到的全部身份字段（id、status='ready'、content_hash=''、
object_etag=''、storage_key、upload_generation）：期间被 repair（轮换 key /
代次 +1 / 翻 uploading）、complete、删行改动过的行一律让位，不会把旧 key 的
HEAD 绑到新身份上。同一 UPDATE 落 ``legacy_etag_bound_at = now()``（DB 钟，
旧 Worker 门禁据此计算存量 URL 的放行时刻，见 jobs/queries/material_claim_gate），
并把 ``upload_generation`` +1：无 hash 行的缓存地址是 ``{id}-v2g{gen}``，命中
不重验，绑定前（未绑窗口内）写入的缓存若不孤儿化，会继续返回与绑定身份不符
的字节。storage_key 不变（存量 key 不含代次段；代次只参与缓存寻址与 complete
的代次守卫——ready 行没有在途 complete）。

恢复重绑（scripts/rebind_material_etags.py）的 CAS 在 material_etag_identity，
不在这里（#1313 线）。
"""

from __future__ import annotations

from typing import Any

from server.app.db.dialect import ConnectSource
from server.app.db.transaction import read_connection, write_transaction

_CANDIDATE_PREDICATE = "status='ready' and content_hash='' and object_etag=''"


def legacy_etag_bind_candidates(
    connect_source: ConnectSource, limit: int, *, after: tuple[Any, Any] | None = None
) -> list[dict[str, Any]]:
    """一批候选行，keyset 分页 (created_at, id)：绑定失败的行不会挡住后续行。"""
    cursor = "and (created_at, id) > (%s, %s)" if after is not None else ""
    params = (*after, limit) if after is not None else (limit,)
    with read_connection(connect_source) as conn:
        rows = conn.execute(
            "select id, workspace_id, storage_key, upload_generation, size_bytes, created_at"
            f" from materials where {_CANDIDATE_PREDICATE} {cursor}"
            " order by created_at, id limit %s",
            params,
        ).fetchall()
    return [dict(row) for row in rows]


def count_legacy_etag_bind_candidates(connect_source: ConnectSource) -> int:
    """剩余候选数（启动线程判断是否有活可干、CLI 报告用）。"""
    with read_connection(connect_source) as conn:
        row = conn.execute(
            f"select count(*) as pending from materials where {_CANDIDATE_PREDICATE}"
        ).fetchone()
    return int(row["pending"]) if row is not None else 0


def bind_legacy_material_etag(
    connect_source: ConnectSource,
    material_id: str,
    *,
    object_etag: str,
    storage_key: str,
    upload_generation: int,
) -> bool:
    """CAS 绑定一行（钉位见模块 docstring）；返回是否落库。"""
    if not object_etag:
        raise ValueError("refusing to bind an empty ETag")
    with write_transaction(connect_source) as conn:
        result = conn.execute(
            "update materials set object_etag=%s, legacy_etag_bound_at=now(),"
            # 代次即身份（#1279 r6 同一机制）：绑定 = 身份从「无」变为 etag，
            # 代次 +1 让绑定前按 {id}-v2g{gen} 写入的 Host / Worker 缓存地址
            # 孤儿化——缓存命中不重验，否则会继续返回与绑定身份不符的字节。
            " upload_generation = upload_generation + 1"
            f" where id=%s and {_CANDIDATE_PREDICATE}"
            " and storage_key=%s and upload_generation=%s",
            (object_etag, material_id, storage_key, upload_generation),
        )
    return result.rowcount > 0
