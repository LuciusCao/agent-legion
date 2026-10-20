"""Runs API contracts (materials-and-runs design §4/§5.2, slice 3)."""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from server.app.routes.run_item_contracts import RunItem


class RunCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[RunItem] = Field(min_length=1)


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


class RunJobStats(BaseModel):
    total: int
    by_status: dict[str, int]


class RunDetailResponse(BaseModel):
    run: RunRecord
    job_stats: RunJobStats
