from pydantic import BaseModel, Field

from server.app.routes.job_view_contracts import JobSummaryResponse


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
