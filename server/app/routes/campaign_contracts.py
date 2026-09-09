"""Campaigns API contracts (#532 PR-A, design §3.2).

Pydantic mirrors of the campaign surface. rerun/upgrade 复用共享的
JobFilterPayload（campaign 与 job-list 过滤器零漂移）；submit 即
POST /runs 的 RunItem union。JSON（inline items）与 multipart（清单
文件）两通道在路由层分流（FastAPI 单路径无法双形态），共用同一
service 入口；JSON body 的字节上限骑 create/preview/upload 上的
campaign body-limit 中间件（见 campaign_body_limit.py），合同层另对
一切计数形缺口（items/job_ids/字段长度）设界。
"""

from __future__ import annotations

from typing import Annotated, Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from server.app.routes.job_batch_filter_contracts import JobFilterPayload
from server.app.routes.run_contracts import MAX_ITEM_ID_LENGTH, RunItem

CampaignMode = Literal["rerun", "submit", "upgrade"]
CampaignStatus = Literal["pending", "running", "paused", "failed", "completed", "cancelled"]

# Inline items count ceiling (PR #541 round-2 P1): FastAPI fully parses the
# JSON body into RunItem models BEFORE any service check runs, so a
# multi-copy-of-the-byte-limit body would balloon memory ahead of the
# manifest_max_bytes 413. The count bound makes the inline channel bounded
# at the contract layer instead — the model list construction is refused at
# 5×10^5 items (~10^3 MB even at a minimal item, comfortably beyond the
# 50 MB byte ceiling the service enforces on the serialized form; the
# multipart channel is already size-bounded at read time). Numbers are
# matched, not derived: the two ceilings bound the same product boundary.
MAX_MANIFEST_ITEMS = 500_000

# Explicit job-id list ceiling (PR #541 round-3 P1): job_ids is the only
# unbounded-length array left on the campaign face (each element is a
# uuid-shaped string, but 10^6 of them is a 40 MB body the item-count and
# byte checks never see — the ids are resolved, never serialized into a
# manifest). 100_000 ids ≈ 4 MB of uuids bounds the list in the same spirit
# as rerun_max_batch_size bounds the feeder's slice: a selection larger than
# this belongs in the filter form (the keyset-cursor design, design §1.4).
MAX_JOB_ID_SELECTION = 100_000


class CampaignKnobsMixin(BaseModel):
    """Per-campaign overrides of the instance defaults (design §2.5)."""

    watermark: int | None = Field(default=None, ge=1)
    batch_size: int | None = Field(default=None, ge=1)


class CampaignRerunTarget(CampaignKnobsMixin):
    """rerun/upgrade: exactly one of job_ids or filter, plus rerun knobs.

    exclude_ids 仅随 filter 形态（allMatching 反选；keyset 取片在 SQL 内
    排除）。显式 job_ids 是手写快照，语义上无排除项、忽略之。
    node_key/from_failed_node 同 JobBatchRerunRequest 规则（仅 rerun；
    upgrade 无节点选择，跨模式规则在 CampaignCreateRequest 判定）。
    """

    node_key: str | None = None
    from_failed_node: bool = False
    # round-3 P1：id 数组在合同层限长（MAX_JOB_ID_SELECTION）——ids 只解析
    # 不序列化，manifest 字节上限看不见这个 body-size 洞。
    job_ids: list[Annotated[str, Field(min_length=1, max_length=MAX_ITEM_ID_LENGTH)]] | None = (
        Field(default=None, max_length=MAX_JOB_ID_SELECTION)
    )
    filter: JobFilterPayload | None = None
    exclude_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def check_selection(self) -> Self:
        if (self.job_ids is None) == (self.filter is None):
            raise ValueError("Provide exactly one of job_ids or filter")
        return self

    def check_rerun_node_selection(self) -> None:
        """node_key / from_failed_node rule (rerun mode only)."""
        if self.from_failed_node:
            if self.node_key is not None:
                raise ValueError("node_key must be None when from_failed_node is True")
        elif not self.node_key:
            raise ValueError("node_key is required when from_failed_node is False")

    def resolved_filter(self) -> Any:

        return self.filter.to_filter() if self.filter is not None else None


class CampaignSubmitInlineTarget(CampaignKnobsMixin):
    """submit, inline channel: a bounded items array in the JSON body."""

    # max_length bounds the body BEFORE Pydantic builds the model list (the
    # round-2 P1 read-then-limit fix): an oversized array fails request
    # validation with a plain 422 instead of deserializing half a million
    # models first.
    items: list[RunItem] = Field(min_length=1, max_length=MAX_MANIFEST_ITEMS)


class CampaignCreateRequest(BaseModel):
    """JSON create body; the multipart variant lives in the route."""

    model_config = ConfigDict(extra="forbid")

    mode: CampaignMode
    # PR-D「任务名称」：骑 target_spec_json（非查询键，不加列——0.8.0 已
    # 发 v80），空串 = UI 派生默认「类型 · 时间」。
    name: str = ""
    rerun: CampaignRerunTarget | None = None
    submit: CampaignSubmitInlineTarget | None = None

    @model_validator(mode="after")
    def check_target(self) -> Self:
        targets = [target for target in (self.rerun, self.submit) if target is not None]
        if len(targets) != 1:
            raise ValueError("Provide exactly one target block (rerun or submit)")
        if self.rerun is not None and self.mode == "submit":
            raise ValueError("mode=submit requires the submit target block")
        if self.submit is not None and self.mode != "submit":
            raise ValueError("the submit target block requires mode=submit")
        if self.rerun is not None and self.mode == "rerun":
            self.rerun.check_rerun_node_selection()
        return self


class CampaignPreviewRequest(CampaignCreateRequest):
    """Dry-run the creation judgements; same shape, no writes."""


class CampaignRecord(BaseModel):
    id: str
    workspace_id: str
    mode: CampaignMode
    status: CampaignStatus
    # 同 CampaignCreateRequest.name——spec 内嵌、空串即 UI 派生默认。
    name: str = ""
    target_spec: dict[str, Any]
    progress: dict[str, Any]
    watermark: int
    batch_size: int
    batches_submitted: int
    jobs_succeeded: int
    jobs_skipped: int
    jobs_failed: int
    error_message: str
    created_by: str
    created_at: str | None
    updated_at: str | None
    finished_at: str | None


class CampaignRunOverview(BaseModel):
    """One linked run of a submit campaign (the detail aggregate, PR-C).

    created_count 是 run 行计数；job_count 是状态计数表的活跃值——部分
    失败后、heal 前二者短暂分歧。"""

    id: str
    status: str
    created_count: int
    job_count: int


class CampaignDetailRecord(CampaignRecord):
    """Detail shape: the record plus the submit-mode run overview."""

    runs: list[CampaignRunOverview] = Field(default_factory=list)


class CampaignCreateResponse(BaseModel):
    campaign: CampaignRecord


class CampaignListResponse(BaseModel):
    campaigns: list[CampaignRecord]


class CampaignDetailResponse(BaseModel):
    campaign: CampaignDetailRecord


class CampaignRerunPreviewResult(BaseModel):
    mode: Literal["rerun", "upgrade"]
    total_count: int
    eligible_count: int
    estimated_batches: int
    batch_size: int


class CampaignSubmitPreviewResult(BaseModel):
    mode: Literal["submit"]
    total_items: int
    would_create: int
    would_skip: int
    estimated_batches: int
    batch_size: int


class CampaignPreviewResponse(BaseModel):
    """Discriminated union of the per-mode preview results (named model so the
    OpenAPI response schema is a $ref, not an inline blob)."""

    result: Annotated[
        CampaignRerunPreviewResult | CampaignSubmitPreviewResult,
        Field(discriminator="mode"),
    ]


class CampaignStatusChangeResponse(BaseModel):
    campaign: CampaignRecord
