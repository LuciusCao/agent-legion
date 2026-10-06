from __future__ import annotations

from datetime import datetime
from typing import Annotated

from pydantic import AfterValidator, BaseModel

from server.app.jobs.queries.failed_run_cursor import parse_failed_run_cursor


def _check_cursor(cursor: str | None) -> str | None:
    if cursor:
        parse_failed_run_cursor(cursor)
    return cursor


# #713：failed-node-runs 按 (finished_at desc, node_run_id desc) keyset 分页；
# 畸形 cursor 在参数校验层 422（与 /jobs/snapshot 的 JobCursor 同一约定），
# 空串即第一页。
FailedRunCursorParam = Annotated[str | None, AfterValidator(_check_cursor)]


class FailedNodeRunItem(BaseModel):
    job_id: str
    node_key: str
    node_run_id: int
    failure_category: str
    failure_detail: str
    error_message: str
    finished_at: datetime | None = None


class FailedNodeRunsResponse(BaseModel):
    runs: list[FailedNodeRunItem]
    # 下一页游标；None = 已到末页（#713）。
    next_cursor: str | None = None
