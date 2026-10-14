"""服务端材料对象写入的唯一 chokepoint：建 intent → PUT → 发布（#1314）。

inline 文本（``run_text_batch``）与 demo seed（``demo_material_seed``）是
仅有的两条服务端 PUT 材料对象的产线（浏览器直传走 presign：行在签 URL
之前已由行锁内派生的 key 引用）。两条都经本函数，次序固定为：

1. 预生成 material_id 与单次使用 key
   ``{ws}/{hash}/v2g0/m_{material_id}/{key_tail}``（#1279 文法）；
2. ``stage_material_intents``：一个事务落无 hash 的 uploading intent 行
   ——**PUT 之前** key 已被行引用；
3. 逐个 PUT + HEAD（空 ETag fail-closed，#1279 R3b）；
4. ``publish_material_intents``：intent 行原子换成 ready 行（或复用 ready
   胜者）；
5. ``release_material_intents``（finally，成功失败都跑）：删除剩余 intent
   行、只删未被任何行引用的自有对象。

不变式（S3 孤儿 GC 材料删除批的承重墙）：自有对象从 PUT 起到发布或补偿
结束，任何已提交时刻都被至少一条 materials 行引用；补偿先删行后删对象，
删行之后再无写者会让行引用这个 key（id 是新 uuid、代次固定 0）。因此 GC
在任何时刻的新鲜反查看到「无行」都意味着该对象永远不会再被引用。

进程在 2–4 之间崩溃会留下 uploading intent 行（引用的对象受保护、不被
GC），与浏览器放弃上传留下的 uploading 行同类：用户可在材料列表删除。
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import Any

from botocore.exceptions import BotoCoreError, ClientError

from server.app.db.dialect import ConnectSource
from server.app.jobs.queries.material_intent import (
    NonReadyPolicy,
    publish_material_intents,
    release_material_intents,
    stage_material_intents,
)
from server.app.storage import ObjectStorage

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class MaterialObjectWrite:
    """一份待写入的服务端材料：内容寻址身份 + 字节 + key 尾段。"""

    content_hash: str
    filename: str
    content_type: str
    payload: bytes
    # key 最后一段：inline 为 ``inline-{uuid4}``（产线标记），demo seed 为文件名。
    key_tail: str


def publish_material_objects(
    connect_source: ConnectSource,
    storage: ObjectStorage,
    workspace_id: str,
    writes: list[MaterialObjectWrite],
    *,
    created_by: str,
    ttl_days: int,
    reuse: dict[str, str] | None = None,
    on_non_ready: NonReadyPolicy = "conflict",
) -> dict[str, str | None]:
    """按模块 docstring 的五步写入并发布；返回 ``content_hash → material id``
    （``on_non_ready='skip'`` 下同 hash 非 ready 行的项为 None）。

    存储故障（boto3 数据面 / OSError，含 HEAD 空 ETag）与 DB 异常原样上抛，
    由调用方映射（inline → 503，demo seed → 降级 warning）。
    """
    intents: list[dict[str, Any]] = []
    for write in writes:
        material_id = uuid.uuid4().hex
        intents.append(
            {
                "material_id": material_id,
                "content_hash": write.content_hash,
                "filename": write.filename,
                "content_type": write.content_type,
                "size_bytes": len(write.payload),
                "storage_key": (
                    f"{workspace_id}/{write.content_hash}/v2g0/m_{material_id}/{write.key_tail}"
                ),
            }
        )
    keys = [intent["storage_key"] for intent in intents]
    try:
        if intents:
            stage_material_intents(connect_source, workspace_id, intents, created_by=created_by)
        for intent, write in zip(intents, writes, strict=True):
            key = intent["storage_key"]
            storage.put_object(key, write.payload, content_type=write.content_type)
            head = storage.head_object(key)
            if head is None or not head.etag:
                raise OSError(f"head after put returned no object/etag: {key}")
            intent["object_etag"] = head.etag
        return publish_material_intents(
            connect_source,
            workspace_id,
            intents,
            reuse=dict(reuse or {}),
            created_by=created_by,
            ttl_days=ttl_days,
            on_non_ready=on_non_ready,
        )
    finally:
        # Also runs after arbitrary DB errors; the original traceback survives.
        _release(connect_source, storage, workspace_id, keys)


def _release(
    connect_source: ConnectSource, storage: ObjectStorage, workspace_id: str, keys: list[str]
) -> None:
    if not keys:
        return
    try:
        referenced = release_material_intents(connect_source, workspace_id, keys)
    except Exception:
        # #204 broad-except audit: commit outcome may be unknown. Keep both the
        # intent rows and the objects (the rows keep GC away from the objects)
        # rather than destroy committed data if the release transaction fails;
        # log the exact key set for recovery, never mask the original cause.
        logger.exception("Cannot release material intents; retaining %s", keys)
        return
    for key in keys:
        if key in referenced:
            continue
        try:
            storage.delete_object(key)
        except (ClientError, BotoCoreError, OSError):
            # Row already gone: the object is an unreferenced single-use key
            # that the S3 orphan GC material delete batch reclaims (#1314).
            logger.exception("Material object cleanup failed: %s", key)
