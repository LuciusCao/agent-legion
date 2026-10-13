"""#1279 恢复面（codex r4 P1 族：ETag fail-closed 检出后的愈合能力）。

两个 JobQueries 门面方法：

- ``request_material_reupload``——消费侧 412（ETag 失配=对象在 complete 后
  被同 URL 覆盖/丢失）的受控修复：行锁内 ready → uploading、清
  object_etag、**代次 +1 并同 presign 重试形态派生新 v2g key**（评审 r5
  P2：只翻状态不轮换时，窗口期内的 bare complete 会在旧 key 上重跑
  核验——无 hash 行 + 同尺寸覆盖会把毒化字节以匹配 etag 静默重新落
  ready；轮换后 bare complete 带旧代次 → 409 代次守卫，无代次则新 key
  缺对象落 failed，两条都安全）。代次链：complete@…g0 → repair 落
  v2g1（不签发 URL、该 key 永无对象）→ 客户端同 hash presign 重试再
  +1 落 v2g2 并签发 URL——代次单调、每代 key 独立，中间代次无对象
  不产生孤儿。此后 dedup 只对 ready 行生效，故重试不再复用坏行。
  历史 job 按 material_id 引用行——重传完成后它们消费的是新
  对象：这正是愈合语义（旧对象已毒化/丢失，412 本来就是它的终局）；
  被换下的旧对象留在旧 key 成为孤儿，由 GC 新文法删除批回收。状态翻转
  在行锁内做（与并发 complete/repair 串行）。
  （无 hash 行的实际重传走「presign 建全新行」——空 hash 不参与
  dedup；被修复的旧行在新 key 缺对象后落 failed，可正常删除。）
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


class MaterialRepairQueriesMixin(ConnectionQueriesMixin):
    def request_material_reupload(self, workspace_id: str, material_id: str) -> None:
        """把 ready 行翻回 uploading：清 object_etag、代次 +1、轮换 v2g key
        （#1279 ETag 失配修复；r5 P2 起轮换 key 使 bare complete 在窗口期
        内要么 409（带旧代次）要么因新 key 缺对象落 failed——无 hash 行的
        size-only 核验再也够不着毒化对象）。

        非 ready 行不需要修复（presign 重试路径本就可用）→ 409；行不存在
        → 404。成功后客户端走 presign → PUT → complete 常规协议重传
        （hash 行：同 hash presign 重试本行；无 hash 行：presign 建新行，
        本行在新 key 缺对象后落 failed 可删除）。
        """
        with self.write() as conn:
            row = conn.execute(
                "select status, content_hash, filename from materials"
                " where id=%s and workspace_id=%s for update",
                (material_id, workspace_id),
            ).fetchone()
            if row is None:
                raise NotFoundError(f"Material not found: {material_id}")
            if str(row["status"]) != "ready":
                raise ConflictError(
                    f"Material {material_id} is {row['status']}, not ready; "
                    "only a ready row with a mismatched ETag needs repair "
                    "(for others, presign already retries)"
                )
            # 与 materials.py presign 重试同形态：锁内代次 +1、由行自身
            # 派生 v2g 新 key（SET 各表达式同读旧行值，两处 +1 一致）。
            conn.execute(
                """
                update materials
                set status='uploading', object_etag='',
                  upload_generation = upload_generation + 1,
                  storage_key = %s || '/v2g' || (upload_generation + 1)
                    || '/m_' || %s || '/' || %s
                where id=%s
                """,
                (
                    f"{workspace_id}/{row['content_hash'] or material_id}",
                    material_id,
                    row["filename"],
                    material_id,
                ),
            )

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
