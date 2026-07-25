from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

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
    job_ids: list[str] = Field(default_factory=list)
    workflow_key: str | None = None


class JobRerunByFailureResultResponse(JobMutationResultResponse):
    rerun_nodes: list[str] = Field(default_factory=list)


class JobRerunByFailureResponse(BaseModel):
    results: list[JobRerunByFailureResultResponse]
