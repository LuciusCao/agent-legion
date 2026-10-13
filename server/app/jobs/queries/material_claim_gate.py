"""#1279 codex r7/r9：材料 ETag 协议门禁的 claim 侧判定（协议 v6）。

统一不变量：无 hash 材料的字节任何时候都不能在没有已验证身份锚的情况下
被消费（锚 = content_hash，或静止期绑定的 etag——见
services/material_etag_backfill.py 的状态机模型）。门禁按行身份状态分档：

- ``never``：hash 行（content_hash ≠ ''）恒免门禁——sha256 重算在
  Host/Worker 两侧本就 fail-closed（毒化 = 响亮失败，非「静默接受」目标
  缺陷），拦它们只让滚动升级窗口内的旧 Worker 白白领不到完全安全的任务
  （可用性成本、零安全收益）；非材料输入同样不拦。
- ``old_worker``：无 hash 且 object_etag ≠ ''——身份已建立，拦协议 < v6
  的 Worker（旧 Worker 忽略描述符的 etag 字段，会静默消费 complete 后被
  同尺寸覆盖的字节）。
- ``all``：无 hash 且 object_etag = ''——身份尚未建立（binder 在途/
  未回填的存量行），拦**所有** Worker：等 binder 绑定，暂时态，留队列
  不落失败（codex r9 P1-2：v5 Worker 若在空值时领走，随后回填建立
  ETag，已提交的 claim 不再复查，活 URL 覆盖窗口仍在）。

Job 的 input_json 现读（候选行不携带它）；行级核验恒按当前 DB 状态
（etag 落列于 complete/回填，可能在 enqueue 之后，冻结 manifest 不含）。
Host 侧物化不过 claim 门禁，其 fail-closed 面在 material_cache /
material_bundle_cache（etag='' 的无 hash ready 行直接报错）。
"""

from __future__ import annotations

from collections.abc import Mapping

from server.app.db.dialect import ConnectSource
from server.app.db.rowmap import parse_object
from server.app.db.transaction import read_connection
from server.app.jobs.queries.connection import ConnectionQueriesMixin

# 门禁档：恒免 / 拦旧 Worker（< v6）/ 拦全部（等 binder 建立身份）。
GATE_NEVER = "never"
GATE_OLD_WORKER = "old_worker"
GATE_ALL = "all"

# 无 hash 行（content_hash=''）才可能进门禁；档由 object_etag 是否已建立分。
_HASHLESS_MATERIAL_SQL = (
    "select object_etag from materials where id=%s and workspace_id=%s and content_hash = ''"
)
_HASHLESS_BUNDLE_SQL = (
    "select mat.object_etag from material_bundle_members m"
    " join materials mat on mat.id = m.material_id"
    " where m.bundle_id = %s and mat.workspace_id=%s and mat.content_hash = ''"
)


def material_claim_gate_tier(connect_source: ConnectSource, workspace_id: str, job_id: str) -> str:
    """该 job 的材料输入的门禁档（GATE_NEVER / GATE_OLD_WORKER / GATE_ALL）。

    行不存在或输入不可解析/非材料 → GATE_NEVER（不拦）。bundle 取成员中
    最高档（all > old_worker > never）。
    """
    with read_connection(connect_source) as conn:
        job = conn.execute(
            "select input_json from jobs where id=%s and workspace_id=%s",
            (job_id, workspace_id),
        ).fetchone()
        if job is None:
            return GATE_NEVER
        input_doc = (
            dict(job["input_json"])
            if isinstance(job["input_json"], Mapping)
            else parse_object(job["input_json"])
        )
        input_type = input_doc.get("type")
        if input_type == "material":
            material_id = str(input_doc.get("material_id") or "")
            if not material_id:
                return GATE_NEVER
            row = conn.execute(_HASHLESS_MATERIAL_SQL, (material_id, workspace_id)).fetchone()
            return _tier_of(row)
        if input_type == "bundle":
            bundle_id = str(input_doc.get("bundle_id") or "")
            if not bundle_id:
                return GATE_NEVER
            rows = conn.execute(_HASHLESS_BUNDLE_SQL, (bundle_id, workspace_id)).fetchall()
            tiers = [_tier_of(row) for row in rows]
            if GATE_ALL in tiers:
                return GATE_ALL
            if GATE_OLD_WORKER in tiers:
                return GATE_OLD_WORKER
            return GATE_NEVER
    return GATE_NEVER


def _tier_of(row) -> str:
    if row is None:
        return GATE_NEVER
    return GATE_OLD_WORKER if str(row["object_etag"]) else GATE_ALL


class MaterialClaimGateQueriesMixin(ConnectionQueriesMixin):
    def job_material_claim_gate_tier(self, workspace_id: str, job_id: str) -> str:
        """门面薄壳：见模块级 ``material_claim_gate_tier``。"""
        return material_claim_gate_tier(self.dsn_identity, workspace_id, job_id)
