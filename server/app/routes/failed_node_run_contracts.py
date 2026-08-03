from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


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
