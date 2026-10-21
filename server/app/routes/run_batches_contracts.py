"""Run-batches task-center contracts (#1157 phase3).

The list/archive surface behind the Studio task center
(docs/architecture/runs-async-mode.md §6): ``RunBatchListItem`` extends the
#1117 batch handle with the archive fields; views are ``active`` (badge +
进行中与排队 section), ``ended`` (waterfall, keyset cursor), ``archived``
(read-only, manual/auto 来源标注).
"""

from typing import Literal

from pydantic import BaseModel, Field

from server.app.routes.run_contracts import RunBatchRecord

RunBatchView = Literal["active", "ended", "archived"]


class RunBatchListItem(RunBatchRecord):
    """任务中心卡片载荷：#1117 句柄 + 归档位与操作归属。

    archive_source：manual = 用户在任务中心手动归档；auto = 终态结束超过
    7 天按查询口径自动归档（不落列）；非归档视图为 null。
    """

    created_by: str
    archived_at: str | None
    archive_source: Literal["manual", "auto"] | None


class RunBatchListResponse(BaseModel):
    """单视图一页；active_count 与视图无关（AppBar 徽标与分区标题同快照）。"""

    batches: list[RunBatchListItem]
    next_cursor: str | None = Field(
        description="keyset 游标（ended/archived 瀑布流）；active 视图不分页恒为 null"
    )
    active_count: int = Field(description="进行中 + 排队数（徽标口径）")


class RunBatchArchiveResponse(BaseModel):
    """手动归档回包：更新后的卡片载荷（幂等语义见 archive_run_batch 的 409）。"""

    batch: RunBatchListItem
