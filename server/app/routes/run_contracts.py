"""Runs API contracts (materials-and-runs design §4/§5.2, slice 3)."""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from server.app.routes.run_item_contracts import RunItem

# #211 Phase 2: request-param deprecation wording (server-side default).
_DEPRECATED_DEFAULT = (
    "Deprecated: defaults to the path workspace_id; removal tracked in #211 (drops by 2026-10-31)."
)
_DEPRECATED_READ = "Deprecated: read workspace_id instead. Since schema v62 the two are equal; removal tracked in #211 (drops by 2026-10-31)."


class RunCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # #211 Phase 2: absent defaults to the path workspace_id (equal since v62).
    workflow_key: str | None = Field(
        default=None, min_length=1, deprecated=True, description=_DEPRECATED_DEFAULT
    )
    items: list[RunItem] = Field(min_length=1)


class RunRecord(BaseModel):
    id: str
    workspace_id: str
    workflow_key: str = Field(description=_DEPRECATED_READ, deprecated=True)
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
    提交后即可拿到 job_id 去 #703 的单 job 端点轮询。"""

    run: RunRecord
    created_count: int
    # #501 全重复治愈路径：created_count=0 时必为空列表（该次提交没有
    # 新建任何 job，jobs 早已由他路补齐）。
    job_ids: list[str] = Field(
        description="本次提交新建的 job id 列表（非 run 全量）；全部 item 已存在时为空数组（重复提交治愈语义，见 #501）。"
    )


class RunListResponse(BaseModel):
    runs: list[RunRecord]


class RunJobStats(BaseModel):
    total: int
    by_status: dict[str, int]


class RunDetailResponse(BaseModel):
    run: RunRecord
    job_stats: RunJobStats
