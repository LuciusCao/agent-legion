"""External artifact-access contracts (#631).

Response models for the workspace-prefixed job read surface external systems
poll after submitting a job: a lightweight status+manifest view, the artifact
listing with execution-distinguishing metadata (content_hash / uploaded_at,
#508), and the raw download (served by the shared raw_response builders, no
model of its own). #739 adds the presigned-download fields to the listing
entries (download_url / expires_at / content_encoding).
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from server.app.routes.external_artifact_entry_contract import ExternalArtifactEntry


class ExternalJobStatusResponse(BaseModel):
    """Lightweight job view for the poll-then-download loop: callers need
    status and the artifact manifest, not the full JobDetail payload."""

    job_id: str
    workspace_id: str
    status: str
    outcome: str = ""
    created_at: datetime | None = None
    updated_at: datetime | None = None
    error_summary: str = ""
    completed_nodes: int = 0
    total_nodes: int = 0
    artifacts: list[str] = Field(default_factory=list)


class ExternalArtifactListResponse(BaseModel):
    model_config = {"extra": "forbid"}

    job_id: str
    workspace_id: str
    status: str
    artifacts: list[ExternalArtifactEntry] = Field(default_factory=list)
    object_storage_enabled: bool = Field(
        description=(
            "False when the instance has no bucket configured: object-backed "
            "artifacts are unreadable and only local job_dir names list"
        )
    )
