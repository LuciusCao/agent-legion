"""#1279 codex r7 / #1316：材料 ETag 协议门禁（协议 v6）的唯一谓词。

滚动升级窗口内（Host 先升、旧 Worker 在役），旧 Worker 忽略 claim 描述符的
``object_etag``，对 presigned GET 无条件下载——仍有活 PUT URL 的无 hash 材料
被同尺寸覆盖后会被静默喂给任务执行（#1279 的目标缺陷）。Host 因此对低于
v6 的 Worker 应用下面这**一个**谓词（``requires_etag_protocol_sql``），在两处
复用：

- 准入层 ``claim_admission.admit_candidate``（读相/写相共用）：job 的任一
  材料/bundle 成员行命中 → 对 < v6 Worker 跳过（留队列，不耗 attempt）；
- 描述符层 ``material_claim_block`` / ``bundle_claim_block``：与描述符同一
  行快照再判一次，命中即拒（claim 500 → lease 过期回收重排），关闭准入与
  描述符组装之间的跨事务窗口（#1316 R12：准入时 etag=''，组装前 repair +
  重传 complete 已落新 etag 与新 key）。

谓词 = ``content_hash = '' 且（status='uploading'，或 ready 且 etag 已绑且
未到存量放行时刻）``：

- hash 行恒免：旧 Worker 对 hash 行有 sha256 fail-closed（毒化 = 响亮失败）；
- ready 且 etag='' 免：没有身份可验（存量行绑定前 / 绑定失败），与 base 的
  size-only 语义一致；
- ready 且 etag 已绑：拦——complete 绑定的行，其 PUT URL 可能仍活；
- 例外：``legacy_etag_bound_at`` 非空（#1316 binder 绑定的存量行）且已过
  ``LEGACY_URL_DRAIN_SECONDS``：升级前签出的 URL 全部过期、此后应用内不再
  为该 key 签发 PUT URL（repair 的 ready 分支轮换 key 并清空本列），旧 Worker
  的无条件 GET 没有被覆盖的通道，放行；
- uploading：拦（#1316 R12——repair 窗口；它在描述符层本就 not ready）；
- failed / expired：不拦，维持既有失败语义（描述符 not ready → 500 → 至多
  requeue_limit 次后 failed），不能让旧 Worker 永久跳过本该失败的任务。

时间全部用 DB 钟（``now()``），与 ``legacy_etag_bound_at`` 同源。
"""

from __future__ import annotations

from collections.abc import Mapping

from server.app.db.dialect import ConnectSource
from server.app.db.rowmap import parse_object
from server.app.db.transaction import read_connection
from server.app.jobs.queries.connection import ConnectionQueriesMixin

# 材料 presign PUT 的有效期（services/materials._PRESIGN_EXPIRY_SECONDS，自
# #141 起恒为 3600，旧版本同值；测试钉二者一致）+ 时钟裕度（签发 Host 钟 /
# DB 钟 / 存储钟偏差，SigV4 的常规容忍量级）。
PRESIGN_PUT_TTL_SECONDS = 3600
CLOCK_SKEW_MARGIN_SECONDS = 900
LEGACY_URL_DRAIN_SECONDS = PRESIGN_PUT_TTL_SECONDS + CLOCK_SKEW_MARGIN_SECONDS


def requires_etag_protocol_sql(alias: str) -> str:
    """旧 Worker 门禁谓词的 SQL 布尔表达式（``alias`` 为 materials 表别名）。

    唯一定义处：准入查询与描述符行快照都嵌入同一表达式（见模块 docstring）。
    """
    a = alias
    return (
        f"({a}.content_hash = '' and ("
        f"{a}.status = 'uploading' or ("
        f"{a}.status = 'ready' and {a}.object_etag <> ''"
        f" and not ({a}.legacy_etag_bound_at is not null"
        f" and {a}.legacy_etag_bound_at"
        f" + make_interval(secs => {LEGACY_URL_DRAIN_SECONDS}) <= now()))))"
    )


_GATED_MATERIAL_SQL = (
    "select 1 from materials mat where mat.id=%s and mat.workspace_id=%s"
    f" and {requires_etag_protocol_sql('mat')}"
)
_GATED_BUNDLE_SQL = (
    "select 1 from material_bundle_members m join materials mat on mat.id = m.material_id"
    f" where m.bundle_id = %s and mat.workspace_id=%s and {requires_etag_protocol_sql('mat')}"
)


def material_inputs_require_etag_protocol(
    connect_source: ConnectSource, workspace_id: str, job_id: str
) -> bool:
    """该 job 的材料输入是否命中门禁谓词（见模块 docstring）。

    claim 裁决处只对旧协议（< v6）Worker 的候选调用（热路径不对 v6+ 付查询）；
    Job 的 input_json 现读（候选行不携带它），行级判定恒按当前 DB 状态。
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
