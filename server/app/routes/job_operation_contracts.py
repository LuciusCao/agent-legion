from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class JobMutationResultResponse(BaseModel):
    job_id: str
    operation: Literal["rerun", "run_to", "continue", "delete", "package"]
    status: Literal["succeeded", "skipped", "failed"]
    node_key: str | None = None
    reason_code: str | None = None
    message: str | None = None


class BatchJobMutationResponse(BaseModel):
    results: list[JobMutationResultResponse]


# Intentionally separate from BatchJobRequest: deletion is a distinct mutation
# contract and may diverge in validation rules even though it currently shares
# the same `job_ids` shape.
class BatchJobIdsRequest(BaseModel):
    job_ids: list[str] = Field(default_factory=list)


class WorkspacePackageRequest(BaseModel):
    job_ids: list[str] = Field(default_factory=list)


class WorkspacePackageResultResponse(BaseModel):
    job_id: str
    status: Literal["succeeded", "failed"]
    reason_code: str | None = None
    message: str | None = None


class WorkspacePackageResponse(BaseModel):
    results: list[WorkspacePackageResultResponse]
    succeeded_count: int
    failed_count: int
    package_filename: str | None = None
    download_url: str | None = None
