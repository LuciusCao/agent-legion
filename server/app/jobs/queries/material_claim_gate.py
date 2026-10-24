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

谓词 = ``content_hash = '' 且（status='uploading'，或 ready 且 etag 已绑）``：

- hash 行恒免：旧 Worker 对 hash 行有 sha256 fail-closed（毒化 = 响亮失败）；
- ready 且 etag='' 免：没有身份可验（存量行绑定前 / 绑定失败），与 base 的
  size-only 语义一致；
- ready 且 etag 已绑：**永久拦，不设排空期**。初稿曾给 #1316 binder 绑定的
  存量行留「绑定时刻 + presign TTL + 时钟裕度（75min）后自动放行」的例外，
  理由是升级前签出的 PUT URL 届时必已全部过期；codex r2 P1 否决——pre-v6
  Worker 可能在升级前已把旧字节缓存在裸 ``material_id`` 地址，消费时命中
  缓存即返回、**根本不发起 GET**，因此「URL 已过期」不能推出「旧 Worker
  缓存的字节匹配已绑 ETag」。旧二进制没有可驱动的缓存失效通道，唯一正确
  的语义就是与 0.8.0 对 complete 绑定行一致的永久门禁：任务保持 queued，
  Worker 升级到 v6 后自动可领（可用性代价 = 混布期间旧 Worker 少领一类
  任务，随升级完成消失；完整性收益 = 已绑字节恒匹配已绑 ETag）。
- uploading：拦（#1316 R12——repair 窗口；它在描述符层本就 not ready）；
- failed / expired：不拦，维持既有失败语义（描述符 not ready → 500 → 至多
  requeue_limit 次后 failed），不能让旧 Worker 永久跳过本该失败的任务。

判定矩阵（行状态 × < v6 Worker）：

| status | hash | etag | 门禁 | 理由 |
| --- | --- | --- | --- | --- |
| ready | 有 | 任意 | 放行 | sha256 fail-closed 兜底 |
| ready | 无 | '' | 放行 | 无身份可验，base size-only 语义 |
| ready | 无 | 已绑（complete / repair / binder / rebind） | **拦（永久）** | 活 URL 覆盖通道 + 旧缓存不可信 |
| uploading | 无 | 任意 | 拦 | repair 窗口（R12），描述符层 not ready |
| failed / expired | 任意 | 任意 | 放行 | 维持既有失败语义 |

谓词不依赖任何时间字段：判定只按当前行快照，DB 钟 / 时钟裕度不参与。
"""

from __future__ import annotations

from collections.abc import Mapping

from server.app.db.dialect import ConnectSource
from server.app.db.rowmap import parse_object
from server.app.db.transaction import read_connection
from server.app.jobs.queries.connection import ConnectionQueriesMixin


def requires_etag_protocol_sql(alias: str) -> str:
    """旧 Worker 门禁谓词的 SQL 布尔表达式（``alias`` 为 materials 表别名）。

    唯一定义处：准入查询与描述符行快照都嵌入同一表达式（见模块 docstring）。
    """
    a = alias
    return (
        f"({a}.content_hash = '' and ("
        f"{a}.status = 'uploading' or ("
        f"{a}.status = 'ready' and {a}.object_etag <> '')))"
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
