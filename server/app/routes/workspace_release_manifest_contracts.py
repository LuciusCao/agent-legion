"""Transport contracts for workspace release manifests (#120)."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class WorkspaceReleaseManifestUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    artifact_names: list[str]
    expected_revision: int = Field(ge=0)


class WorkspaceReleaseManifestRecord(BaseModel):
    workspace_id: str
    revision: int = Field(ge=0)
    artifact_names: list[str]
    manifest_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    created_by: str
    created_at: str | None


class WorkspaceReleaseManifestHistoryResponse(BaseModel):
    revisions: list[WorkspaceReleaseManifestRecord]


class WorkspaceReleaseManifestConflictDetail(BaseModel):
    message: str
    expected_revision: int
    current_manifest: WorkspaceReleaseManifestRecord


class WorkspaceReleaseManifestConflictResponse(BaseModel):
    detail: WorkspaceReleaseManifestConflictDetail
