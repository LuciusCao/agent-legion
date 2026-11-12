"""服务端材料对象写入的「行先于对象」协议——queries 层（#1314）。

inline 文本与 demo seed 两条服务端产线经
``services/material_object_publish.py`` 共用本模块（ConnectSource 驱动的
模块级函数：demo seed 可能只有 DSN，没有 JobQueries 门面）：

1. ``stage_material_intents``——PUT 之前一个事务落 **无 hash 的 uploading
   intent 行**（key 已定、``content_hash=''``）。从这一刻起对象 key 被一条
   materials 行引用，S3 孤儿 GC 的新鲜反查必见；无 hash 的行不占
   ``(workspace_id, content_hash)`` 去重槽，presign 也就无法按 hash 接管
   它（presign 只按 hash 找旧行），并发同内容请求的胜负仍在发布时决出。
2. ``publish_material_intents``——PUT + HEAD 之后一个事务（workspace 级
   advisory 锁，按 hash 排序）：CAS 删除自己的 intent 行（id + key +
   uploading + 无 hash），同 id 插 ready 行（``on conflict`` 落败则复用
   ready 胜者或按策略处置）。删 intent 与插 ready 同事务提交，任何已提交
   时刻对象 key 都至少被一条行引用——GC 反查看不到「无行」的中间态。
   intent 行已不在（用户删除了 uploading 行 / 被 complete 改了状态）→
   ConflictError，绝不发布指向可能已被删对象的 ready 行。
3. ``release_material_intents``——收尾（成功与失败都跑）：同一 advisory
   锁下删除仍是 intent 形态的自有行，并返回仍被引用的 key；调用方只删
   未被引用的对象。提交确认丢失时已发布的 ready 行在引用集合里，对象
   保留。

advisory 锁名沿用 ``inline-material:{workspace_id}``（与 #1312 期 Host 的
inline 发布同名，混合版本下仍互斥）。
"""

from __future__ import annotations

from typing import Any, Literal

from server.app.db.dialect import ConnectSource
from server.app.db.transaction import write_transaction
from server.app.services.job_errors import ConflictError

NonReadyPolicy = Literal["conflict", "skip"]

_LOCK_SQL = "select pg_advisory_xact_lock(hashtextextended(%s, 0))"
_STAGE_SQL = (
    "insert into materials(id, workspace_id, content_hash, filename, content_type,"
    " size_bytes, storage_key, status, created_by)"
    " values (%s, %s, '', %s, %s, %s, %s, 'uploading', %s)"
)
_DROP_INTENT_SQL = (
    "delete from materials where id=%s and workspace_id=%s and storage_key=%s"
    " and status='uploading' and content_hash='' returning id"
)
_INSERT_READY_SQL = (
    "insert into materials(id, workspace_id, content_hash, filename, content_type,"
    " size_bytes, storage_key, status, created_by, expires_at, object_etag)"
    " values (%s, %s, %s, %s, %s, %s, %s, 'ready', %s,"
    " case when %s > 0 then now() + make_interval(days => %s) end, %s)"
    " on conflict (workspace_id, content_hash) where content_hash <> '' do nothing"
    " returning id, status"
)
_LOOKUP_FOR_UPDATE_SQL = (
    "select id, status from materials where workspace_id=%s and content_hash=%s for update"
)


def _lock(conn: Any, workspace_id: str) -> None:
    conn.execute(_LOCK_SQL, (f"inline-material:{workspace_id}",))


def stage_material_intents(
    connect_source: ConnectSource,
    workspace_id: str,
    intents: list[dict[str, Any]],
    *,
    created_by: str,
) -> None:
    """PUT 之前落无 hash 的 uploading intent 行（一个事务，见模块 docstring）。"""
    with write_transaction(connect_source) as conn:
        for intent in intents:
            conn.execute(
                _STAGE_SQL,
                (
                    intent["material_id"],
                    workspace_id,
                    intent["filename"],
                    intent["content_type"],
                    intent["size_bytes"],
                    intent["storage_key"],
                    created_by,
                ),
            )


def publish_material_intents(
    connect_source: ConnectSource,
    workspace_id: str,
    intents: list[dict[str, Any]],
    *,
    reuse: dict[str, str],
    created_by: str,
    ttl_days: int,
    on_non_ready: NonReadyPolicy = "conflict",
) -> dict[str, str | None]:
    """把 intent 行原子换成 ready 行，并在同一锁下复核待复用的 ready 行。

    返回 ``content_hash → material id``：自己发布的行、或 ``on conflict``
    落败后复用的 ready 胜者；``on_non_ready='skip'`` 时同 hash 的非 ready
    行使该项为 None（自己的 intent 已删，对象由 release 回收）。``reuse``
    是调用方预检时选中的 ready 行（hash → id），此刻必须仍是同一条
    ready 行，否则 ConflictError 整批回滚。
    """
    by_hash = {intent["content_hash"]: intent for intent in intents}
    result: dict[str, str | None] = {}
    with write_transaction(connect_source) as conn:
        _lock(conn, workspace_id)
        for digest in sorted({*by_hash, *reuse}):
            intent = by_hash.get(digest)
            row = None
            if intent is not None:
                dropped = conn.execute(
                    _DROP_INTENT_SQL,
                    (intent["material_id"], workspace_id, intent["storage_key"]),
                ).fetchone()
                if dropped is None:
                    raise ConflictError(
                        "Material staging row was removed or changed concurrently; retry"
                    )
                row = conn.execute(
                    _INSERT_READY_SQL,
                    (
                        intent["material_id"],
                        workspace_id,
                        digest,
                        intent["filename"],
                        intent["content_type"],
                        intent["size_bytes"],
                        intent["storage_key"],
                        created_by,
                        ttl_days,
                        ttl_days,
                        intent["object_etag"],
                    ),
                ).fetchone()
            if row is None:
                row = conn.execute(_LOOKUP_FOR_UPDATE_SQL, (workspace_id, digest)).fetchone()
            expected_id = reuse.get(digest)
            if (
                row is not None
                and row["status"] != "ready"
                and intent is not None
                and on_non_ready == "skip"
            ):
                result[digest] = None
                continue
            if (
                row is None
                or row["status"] != "ready"
                or (expected_id is not None and row["id"] != expected_id)
            ):
                raise ConflictError(
                    "Material for text content is not ready or changed; "
                    "finish the upload or remove the non-ready material before retrying"
                )
            result[digest] = str(row["id"])
    return result


def release_material_intents(
    connect_source: ConnectSource, workspace_id: str, keys: list[str]
) -> set[str]:
    """删除仍是 intent 形态的自有行，返回 ``keys`` 中仍被任意行引用的子集。

    与发布同一 advisory 锁：等待在途发布的 COMMIT 落定后再判定引用，提交
    确认丢失时已发布的 ready 行在返回集合里，对象不会被误删。
    """
    with write_transaction(connect_source) as conn:
        _lock(conn, workspace_id)
        conn.execute(
            "delete from materials where workspace_id=%s and storage_key = ANY(%s)"
            " and status='uploading' and content_hash=''",
            (workspace_id, keys),
        )
        rows = conn.execute(
            "select storage_key from materials where workspace_id=%s and storage_key = ANY(%s)",
            (workspace_id, keys),
        ).fetchall()
    return {str(row["storage_key"]) for row in rows}
