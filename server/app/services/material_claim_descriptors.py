"""Worker-facing material / bundle descriptors for the claim response (design §6.2).

Split from ``material_cache`` / ``material_bundle_cache`` (file budgets): the
claim response path rebuilds these memory-only descriptors (presigned GET
URLs, the row's ETag and upload generation) from one row snapshot, and the
old-Worker ETag-protocol gate (#1279 v6 / #1316 R12) is re-checked on that
same snapshot here.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from server.app.db.dialect import ConnectSource
from server.app.services.material_bundle_cache import _ready_bundle
from server.app.services.material_cache import _ready_row, _require_storage, input_document
from server.app.storage import ObjectStorage
from shared.material_cache import MaterializeError
from shared.protocol import MATERIAL_ETAG_PROTOCOL_VERSION, PROTOCOL_VERSION


def assert_worker_may_consume(
    rows: Iterable[Mapping[str, Any]], worker_protocol_version: int, kind: str
) -> None:
    """描述符层的旧 Worker 门禁（#1316 R12）：与 claim admission 同一谓词
    （jobs/queries/material_claim_gate.requires_etag_protocol_sql），但判在
    **描述符所用的同一行快照**上（行由 ``_ready_row`` / ``_ready_bundle`` 连同
    ``requires_etag_protocol`` 列一起读出）。

    admission 与描述符组装分属两个事务：准入时行可能还是 etag=''（放行），
    组装前 repair + 重传 complete 已落新 etag 与新 key（或 binder 刚绑定）。
    旧 Worker 会忽略描述符里的 etag 做无条件 GET，所以命中谓词就拒绝本次
    claim：MaterializeError → claim 响应 500 → Worker 丢弃、lease 过期回收
    重排（消耗一次 attempt），下一轮 admission 已按新状态跳过它。
    """
    if worker_protocol_version >= MATERIAL_ETAG_PROTOCOL_VERSION:
        return
    for row in rows:
        if row.get("requires_etag_protocol"):
            raise MaterializeError(
                f"{kind} {row['id']} requires Worker protocol"
                f" v{MATERIAL_ETAG_PROTOCOL_VERSION}+ (ETag-verified download);"
                f" this Worker speaks v{worker_protocol_version}"
            )


def material_claim_block(
    connect_source: ConnectSource,
    workspace_id: str,
    job: Mapping[str, Any],
    *,
    storage: ObjectStorage | None = None,
    download_expires_seconds: int = 3600,
    worker_protocol_version: int = PROTOCOL_VERSION,
) -> dict[str, Any] | None:
    """The Worker-facing material descriptor for the claim response.

    Injected into the claim-time ``runtime_context`` (memory only, like the
    secret injection — the persisted manifest keeps only the audit stub).
    The Worker materializes from the presigned GET URL with no storage
    credentials of its own; ``storage_key`` never crosses the wire. Bundle
    inputs get one presigned GET per member (#156). ``worker_protocol_version``
    is the claiming Worker's (the claim response passes it explicitly): an
    old Worker is refused on the descriptor's own row snapshot (#1316 R12,
    ``assert_worker_may_consume``).
    """
    input_doc = input_document(job)
    if input_doc.get("type") == "bundle":
        return bundle_claim_block(
            connect_source,
            workspace_id,
            job,
            storage=storage,
            download_expires_seconds=download_expires_seconds,
            worker_protocol_version=worker_protocol_version,
        )
    if input_doc.get("type") != "material":
        return None
    row = _ready_row(connect_source, workspace_id, input_doc)
    material_id = str(row["id"])
    assert_worker_may_consume([row], worker_protocol_version, "material")
    storage = _require_storage(storage, material_id)
    return {
        "material_id": material_id,
        "filename": str(row.get("filename") or ""),
        "content_type": str(row.get("content_type") or ""),
        "size_bytes": int(row.get("size_bytes") or 0),
        "content_hash": str(row.get("content_hash") or ""),
        # #1279：行记录的 ETag 与代次随描述符下发（memory-only，不落
        # manifest）；Worker 对 presigned GET 带 If-Match 核验，
        # upload_generation 参与无 hash 行的缓存寻址（代次即身份，见
        # material_cache_address）——与 download_url 取自同一行快照，地址
        # 代次与 URL key 代次恒一致。旧 Server 不下发这两字段（Worker 按
        # 缺失回落）；**旧 Worker（协议 < v6）领不到命中门禁的材料任务**
        # （claim admission 跳过，见 shared/protocol.py v6 条目与
        # jobs/queries/material_claim_gate.py），不存在「忽略字段仍执行」
        # 的窗口。
        "object_etag": str(row.get("object_etag") or ""),
        "upload_generation": int(row["upload_generation"]),
        "download_url": storage.presign_get(
            str(row["storage_key"]), expires_seconds=download_expires_seconds
        ),
    }


def bundle_claim_block(
    connect_source: ConnectSource,
    workspace_id: str,
    job: Mapping[str, Any],
    *,
    storage: ObjectStorage | None = None,
    download_expires_seconds: int = 3600,
    worker_protocol_version: int = PROTOCOL_VERSION,
) -> dict[str, Any] | None:
    """The Worker-facing bundle descriptor: one presigned GET per member.

    Every member is checked against the old-Worker gate on the same member
    snapshot the descriptor is built from (#1316 R12, see
    ``assert_worker_may_consume``).
    """
    input_doc = input_document(job)
    if input_doc.get("type") != "bundle":
        return None
    bundle, members = _ready_bundle(connect_source, workspace_id, input_doc)
    bundle_id = str(bundle["id"])
    assert_worker_may_consume(members, worker_protocol_version, "bundle member")
    storage = _require_storage(storage, bundle_id)
    return {
        "material_id": bundle_id,
        "kind": "bundle",
        "filename": str(bundle.get("name") or ""),
        "size_bytes": int(bundle.get("total_size_bytes") or 0),
        "entries": [
            {
                "material_id": str(member["id"]),
                "path": str(member["path"]),
                "size_bytes": int(member.get("size_bytes") or 0),
                "content_hash": str(member.get("content_hash") or ""),
                # #1279：每成员的行记录 ETag + 代次（memory-only）；Worker
                # 逐成员对 presigned GET 带 If-Match 核验、代次参与无 hash
                # 成员缓存寻址，均与 download_url 同行快照；旧 Server 不下发
                # （Worker 按缺失回落），旧 Worker（协议 < v6）领不到命中
                # 门禁的材料任务（claim admission 跳过）。
                "object_etag": str(member.get("object_etag") or ""),
                "upload_generation": int(member["upload_generation"]),
                "download_url": storage.presign_get(
                    str(member["storage_key"]), expires_seconds=download_expires_seconds
                ),
            }
            for member in members
        ],
    }
