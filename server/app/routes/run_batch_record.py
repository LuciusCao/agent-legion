"""Shared async-run batch handle and its read-only feeding-gate context."""

from typing import Any

from pydantic import BaseModel, Field


class FeedingGateFields(BaseModel):
    feeding_gated: bool | None = Field(
        default=None, description="未完成投喂是否正被 workspace 积压水位门控"
    )
    workspace_backlog: int | None = Field(
        default=None, ge=0, description="派生快照中的 pending + running 任务数"
    )
    watermark: int | None = Field(
        default=None, ge=0, description="feeder 当前生效的 workspace 积压水位"
    )


class RunBatchRecord(FeedingGateFields):
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
