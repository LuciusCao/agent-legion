"""#1279 恢复面（codex r4 P1 族：ETag fail-closed 检出后的愈合能力）。

- ``repair_material_for_reupload_tx``——消费侧 412（ETag 失配=对象在
  complete 后被同 URL 覆盖/丢失）的受控修复（行翻转，编排与 URL 签发在
  ``MaterialsService.request_material_reupload``）：行锁内 ready →
  uploading、清 object_etag、**代次 +1 并同 presign 重试形态派生新 v2g
  key**（r5 P2：只翻状态不轮换时，窗口期内的 bare complete 会在旧 key
  上重跑核验——无 hash 行 + 同尺寸覆盖会把毒化字节以匹配 etag 静默
  重新落 ready；轮换后 bare complete 带旧代次 → 409 代次守卫，无代次则
  新 key 缺对象落 failed，两条都安全）。修复为**原行轮换后的新 key**
  签发受代次保护的 PUT URL（codex r5 P1：无 hash 行没有「presign 按
  hash 找旧行」的回路，presign 只会建新行、历史引用永不合愈）——hash /
  无 hash 两格统一由原 material_id 的 URL 重传，complete（携带轮换后
  代次）落 ready 后历史 job/bundle 引用即愈合（它们按 material_id 消费
  新对象——旧对象已毒化/丢失，412 是它的终局；旧对象留在旧 key 成为
  孤儿，由 bucket lifecycle 回收，GC 材料删除批重开见 #1314）。代次链：
  complete@…g0 → repair 落 v2g1 并签发
  URL——与 presign 重试同构、代次单调、每代 key 独立。
  uploading/failed 行 = 修复重试（上次 repair 与重传之间的崩溃恢复）：
  不轮换，返回现状 key/代次供重签 URL；expired → 409。
- 恢复重绑两方法（S3 快照恢复后 AWS CLI 分段上传改变 ETag 的场景，见
  docs/backup-restore-runbook.md）：``materials_with_object_etag`` 给出
  扫描面，``rebind_material_etag`` 以 (id, expected_etag) CAS 落新值——
  调用方（scripts/rebind_material_etags.py）必须先核验恢复字节
  （size 恒验 + content_hash 非空时流式 sha256）再重绑，字节不符
  fail-closed。
"""

from __future__ import annotations

from typing import Any

from server.app.jobs.queries.connection import ConnectionQueriesMixin
from server.app.services.job_errors import ConflictError, NotFoundError


def repair_material_for_reupload_tx(
    conn: Any, workspace_id: str, material_id: str
) -> dict[str, Any]:
    """行锁内把 ready 行翻回 uploading（清 etag、代次 +1、轮换 v2g key），
    返回重传身份 ``{material_id, upload_generation, storage_key,
    size_bytes}``（#1279 ETag 失配修复；编排与 PUT URL 签发在
    ``MaterialsService.request_material_reupload``）。

    - 行不存在 → 404；expired → 409（终态，TTL 语义）；
    - ready → 翻转 + 轮换（r5 P2：bare complete 随后要么 409 要么因新
      key 缺对象落 failed，size-only 核验够不着毒化对象）；
    - uploading / failed → 不翻转不轮换，返回现状身份供重签 URL
      （repair→PUT 之间崩溃后的幂等重跑；与正常上传在途行共享该形态，
      重签 URL 与首发 URL 同能力，无新风险面）。
    """
    row = conn.execute(
        "select status, content_hash, filename, size_bytes,"
        " upload_generation, storage_key from materials"
        " where id=%s and workspace_id=%s for update",
        (material_id, workspace_id),
    ).fetchone()
    if row is None:
        raise NotFoundError(f"Material not found: {material_id}")
    status = str(row["status"])
    if status == "expired":
        raise ConflictError(f"Material is expired: {material_id}")
    generation = int(row["upload_generation"])
    storage_key = str(row["storage_key"])
    if status == "ready":
        # 与 materials.py presign 重试同形态：锁内代次 +1、由行自身派生
        # v2g 新 key（SET 各表达式同读旧行值，两处 +1 一致）。
        generation += 1
        storage_key = (
            f"{workspace_id}/{row['content_hash'] or material_id}"
            f"/v2g{generation}/m_{material_id}/{row['filename']}"
        )
        conn.execute(
            "update materials set status='uploading', object_etag='',"
            " upload_generation=%s, storage_key=%s where id=%s",
            (generation, storage_key, material_id),
        )
    return {
        "material_id": material_id,
        "upload_generation": generation,
        "storage_key": storage_key,
        "size_bytes": int(row["size_bytes"]),
    }


class MaterialRepairQueriesMixin(ConnectionQueriesMixin):
    def materials_with_object_etag(self) -> list[dict[str, Any]]:
        """恢复重绑扫描面：记录了 object_etag 的全部材料行（跨 workspace）。

        运维低频全量扫描（恢复流程专用），无索引可接受。
        """
        with self._connect_read() as conn:
            rows = conn.execute(
                "select id, workspace_id, content_hash, size_bytes, storage_key,"
                " object_etag from materials where object_etag <> ''"
            ).fetchall()
        return [dict(row) for row in rows]

    def rebind_material_etag(self, material_id: str, *, expected_etag: str, new_etag: str) -> bool:
        """CAS 重绑：仅当行的 object_etag 仍是 ``expected_etag`` 时落
        ``new_etag``；返回是否重绑（False = 期间被并发改动，调用方报告并
        交下一轮处理）。"""
        with self.write() as conn:
            result = conn.execute(
                "update materials set object_etag=%s where id=%s and object_etag=%s",
                (new_etag, material_id, expected_etag),
            )
        return result.rowcount > 0
