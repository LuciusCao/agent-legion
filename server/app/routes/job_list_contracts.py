from typing import Annotated

from pydantic import AfterValidator, BaseModel, Field

from server.app.jobs.queries.job_pagination import parse_job_cursor
from server.app.routes.job_view_contracts import JobSummaryResponse


def _check_cursor(cursor: str | None) -> str | None:
    if cursor:
        parse_job_cursor(cursor)
    return cursor


# #891：/jobs/snapshot 的 cursor 解析失败（缺分隔符、时间戳非法等）在参数
# 校验层 422（与 limit 越界同一约定），带可读 detail——不再落到 SQL 抛未处理
# 异常成 5xx，让按错误码表「5xx 退避重试」的调用方对永远失败的参数无限重试。
# 空串仍是第一页（恒等 no-op）。
JobCursor = Annotated[str | None, AfterValidator(_check_cursor)]


class JobsPageResponse(BaseModel):
    workspace_id: str
    revision: int
    # The filtered total and per-status stats are only computed on the first
    # page; cursor pages return total=None and stats={} to skip the
    # workspace-wide aggregations.
    total: int | None = None
    stats: dict[str, int] = Field(default_factory=dict)
    jobs: list[JobSummaryResponse]
    next_cursor: str | None = None


class JobFacetsResponse(BaseModel):
    workspace_id: str
    total: int
    status_counts: dict[str, int]
    # workflow_version keys are stringified ints; jobs without a version are
    # keyed "none".
    version_counts: dict[str, int]
    # Jobs without a running/failed node are keyed "" (empty string).
    node_counts: dict[str, int]
