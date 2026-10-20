"""Runs API contracts (materials-and-runs design §4/§5.2, slice 3).

#1117 adds the async accept mode: ``mode="async"`` on the create request
returns 202 with the run + batch handle (``RunAcceptedResponse``); the detail
endpoint carries the same handle on its nullable ``batch`` field, and the
pause/resume/cancel control endpoints answer ``RunBatchResponse``. Design:
docs/architecture/runs-async-mode.md.
"""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from server.app.routes.run_item_contracts import RunItem


class RunCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[RunItem] = Field(min_length=1)
    # sync（缺省/None）= 既有请求内创建；async = 受理即返（202），feeder 后台
    # 解析/去重/建 job。None 而非 "sync" 默认值：openapi-typescript 把带
    # default 的字段生成为必填，会破坏既有前端调用点（M43 不动 Studio UI）。
    mode: Literal["sync", "async"] | None = None


class RunRecord(BaseModel):
    id: str
    workspace_id: str
    source_kind: str
    status: str
    created_count: int
    error_message: str
    frozen_pins: dict[str, Any]
    stats: dict[str, Any]
    created_by: str
    created_at: str | None
    updated_at: str | None


class RunCreateResponse(BaseModel):
    """#467 A4 响应瘦身保持：run + created_count only，永不物化 job 行
    （万级 items 的响应体积回归由测试钉住）；#735 加回 job_ids——服务层
    本就返回的字符串 id 列表（体积与 job rows 差一个数量级），外部系统
    提交后即可拿到 job_id 去 #703 的单 job 端点轮询。#1157 phase0 加
    skipped_count 标量计数（不随规模涨体积）。"""

    run: RunRecord
    created_count: int
    # #501 全重复治愈路径：created_count=0 时必为空列表（该次提交没有
    # 新建任何 job，jobs 早已由他路补齐）。
    job_ids: list[str] = Field(
        description="本次提交新建的 job id 列表（非 run 全量）；全部 item 已存在时为空数组（重复提交治愈语义，见 #501）。"
    )
    skipped_count: int = Field(
        description="本次提交被跳过（未新建）的条目数：条目已存在对应任务的 dedup 过滤，"
        "加上写入竞态中被并发提交先行认领的条目；created_count + skipped_count == 本次提交条目数。"
    )


class RunListResponse(BaseModel):
    runs: list[RunRecord]


class RunBatchRecord(BaseModel):
    """异步 run 的 feeder 句柄（#1117）：状态机 + 游标 + 计数器。

    jobs_skipped 由调用方按 processed_count − jobs_created 推导（dedup 过滤
    与 chunk 跳过同源，不单独物化）；chunk_errors 至多 20 条（intake 先例）。
    """

    run_id: str
    status: str = Field(
        description="pending/running/paused/completed/failed/cancelled（语义见 runs-async-mode.md §2）"
    )
    items_total: int
    processed_count: int = Field(description="游标：已处理的条目数（含 dedup 跳过与失败 chunk）")
    jobs_created: int = Field(
        description="该 run 当前实有 job 数（每 tick 按 count_jobs_in_run 重算）"
    )
    chunk_errors: list[dict[str, Any]]
    error_message: str
    created_at: str | None
    updated_at: str | None
    finished_at: str | None


class RunAcceptedResponse(BaseModel):
    """202 异步受理（#1117）：run 记录 + batch 句柄；job 由 feeder 后台创建，
    轮询 GET /workspaces/{id}/runs/{run_id}（batch + job_stats）取进度。"""

    run: RunRecord
    batch: RunBatchRecord


class RunBatchResponse(RunAcceptedResponse):
    """pause/resume/cancel 控制面回包（#1117）：与 202 受理同形。"""


class RunJobStats(BaseModel):
    total: int
    by_status: dict[str, int]


class RunDetailResponse(BaseModel):
    run: RunRecord
    job_stats: RunJobStats
    # #1117: 仅异步 run 携带 batch 句柄；同步 run 恒为 null。
    batch: RunBatchRecord | None = None
