"""#1279 codex r7 P1：材料 ETag 协议门禁的 claim 侧判定查询（协议 v6）。

滚动升级窗口内（Host 先升、旧 Worker 在役），旧 Worker 忽略 claim 描述符的
``object_etag``，对 presigned GET 无条件下载——complete 后同尺寸覆盖的无
hash 材料会被静默喂给任务执行（本 PR 的目标缺陷）。Host 因此在 claim 裁决
（``claim_admission.admit_candidate``，读相/写相共用）对低于 v6 的 Worker
跳过这类任务：留队列等升级，不落失败。

门禁谓词 = 「材料输入含 object_etag 非空且 content_hash 为空的行」：

- hash 行豁免：旧 Worker 对 hash 行有 sha256 fail-closed（毒化 = 响亮失败，
  非「静默接受」目标缺陷）；拦它们只让旧 Worker 白白领不到完全安全的任务
  （可用性成本、零安全收益）；
- etag='' 行（存量/旧服务）豁免：无 etag 可验，门禁无意义；
- 即门禁恰好只覆盖目标缺陷格：无 hash 且已记录内容身份（etag）的行。

JobQueries 门面方法（BOUNDARY-DATA-001）：按 job 的 input_json 解析
material / bundle 引用并查 materials 行。
"""

from __future__ import annotations

from collections.abc import Mapping

from server.app.db.dialect import ConnectSource
from server.app.db.rowmap import parse_object
from server.app.db.transaction import read_connection
from server.app.jobs.queries.connection import ConnectionQueriesMixin

_GATED_MATERIAL_SQL = (
    "select 1 from materials where id=%s and workspace_id=%s"
    " and object_etag <> '' and content_hash = ''"
)
_GATED_BUNDLE_SQL = (
    "select 1 from material_bundle_members m join materials mat on mat.id = m.material_id"
    " where m.bundle_id = %s and mat.workspace_id=%s"
    " and mat.object_etag <> '' and mat.content_hash = ''"
)


def material_inputs_require_etag_protocol(
    connect_source: ConnectSource, workspace_id: str, job_id: str
) -> bool:
    """该 job 的材料输入是否命中 ETag 协议门禁（谓词见模块 docstring）。

    claim 裁决处对每个旧协议（< v6）Worker 的候选调用；Job 的
    input_json 现读（候选行不携带它），行级核验恒按当前 DB 状态
    （etag 是 complete 时落列的，enqueue 冻结的 manifest 不含）。
    输入不可解析/非材料/无引用 → False（不拦）。``connect_source`` 是
    ConnectSource（facade 直通或裸 DSN，BOUNDARY-DATA-001 #187）。
    """
    with read_connection(connect_source) as conn:
        job = conn.execute(
            "select input_json from jobs where id=%s and workspace_id=%s",
            (job_id, workspace_id),
        ).fetchone()
        if job is None:
            return False
        input_doc = (
            dict(job["input_json"])
            if isinstance(job["input_json"], Mapping)
            else parse_object(job["input_json"])
        )
        input_type = input_doc.get("type")
        if input_type == "material":
            material_id = str(input_doc.get("material_id") or "")
            if not material_id:
                return False
            row = conn.execute(_GATED_MATERIAL_SQL, (material_id, workspace_id)).fetchone()
            return row is not None
        if input_type == "bundle":
            bundle_id = str(input_doc.get("bundle_id") or "")
            if not bundle_id:
                return False
            row = conn.execute(_GATED_BUNDLE_SQL, (bundle_id, workspace_id)).fetchone()
            return row is not None
    return False


class MaterialClaimGateQueriesMixin(ConnectionQueriesMixin):
    def job_material_inputs_require_etag_protocol(self, workspace_id: str, job_id: str) -> bool:
        """门面薄壳：见模块级 ``material_inputs_require_etag_protocol``。"""
        return material_inputs_require_etag_protocol(self.dsn_identity, workspace_id, job_id)
