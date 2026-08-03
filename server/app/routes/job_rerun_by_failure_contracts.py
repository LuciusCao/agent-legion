from __future__ import annotations

from typing import Literal, Self

from pydantic import BaseModel, Field, model_validator

from server.app.routes.job_batch_filter_contracts import JobFilterPayload
from server.app.routes.job_operation_contracts import JobMutationResultResponse


class JobRerunByFailureRequest(BaseModel):
    category: Literal["technical", "business", "unknown"]
    strategy: Literal["auto", "rerun_self", "rerun_upstream"] = "auto"
    # Optional explicit rerun start node: jobs whose matching failure is the
    # node itself or one of its downstream nodes rerun from this node instead
    # of the strategy-derived target; other selected jobs are skipped.
    from_node_key: str | None = None
    # Empty job_ids + no filter selects every job with a matching failed run.
    job_ids: list[str] = Field(default_factory=list)
    filter: JobFilterPayload | None = None
    exclude_ids: list[str] = Field(default_factory=list)
    workflow_key: str | None = None

    @model_validator(mode="after")
    def check_job_selection(self) -> Self:
        if self.filter is not None and self.job_ids:
            raise ValueError("job_ids and filter are mutually exclusive")
        return self


class JobRerunByFailureResultResponse(JobMutationResultResponse):
    rerun_nodes: list[str] = Field(default_factory=list)


class JobRerunByFailureResponse(BaseModel):
    results: list[JobRerunByFailureResultResponse]
