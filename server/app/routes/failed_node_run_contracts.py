from __future__ import annotations

from datetime import datetime
from typing import Literal, Self

from pydantic import BaseModel, Field, model_validator

from server.app.routes.job_batch_filter_contracts import JobFilterPayload
from server.app.routes.job_operation_contracts import JobMutationResultResponse


class FailedNodeRunItem(BaseModel):
    job_id: str
    node_key: str
    node_run_id: int
    workflow_key: str
    failure_category: str
    failure_detail: str
    error_message: str
    finished_at: datetime | None = None


class FailedNodeRunsResponse(BaseModel):
    runs: list[FailedNodeRunItem]


class JobRerunByFailureRequest(BaseModel):
    category: Literal["technical", "business", "unknown"]
    strategy: Literal["auto", "rerun_self", "rerun_upstream"] = "auto"
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
