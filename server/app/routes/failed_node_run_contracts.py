from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class FailedNodeRunItem(BaseModel):
    job_id: str
    node_key: str
    node_run_id: int
    workflow_key: str = Field(
        description=(
            "Deprecated: filter by the workspace the rows were fetched from "
            "instead (the list endpoint is workspace-scoped; the value always "
            "equals that workspace's id since schema v62). Removal is tracked "
            "in #211."
        ),
        deprecated=True,
    )
    failure_category: str
    failure_detail: str
    error_message: str
    finished_at: datetime | None = None


class FailedNodeRunsResponse(BaseModel):
    runs: list[FailedNodeRunItem]
