from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


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
