"""External artifact-access contracts (#631).

Response models for the workspace-prefixed job read surface external systems
poll after submitting a job: a lightweight status+manifest view, the artifact
listing with execution-distinguishing metadata (content_hash / uploaded_at,
#508), and the raw download (served by the shared raw_response builders, no
model of its own).
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


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


class ExternalArtifactEntry(BaseModel):
    """One manifest entry: ``storage=object`` rows come from the authoritative
    job_artifacts manifest (size/content_hash/uploaded_at distinguish the
    current execution after a rerun, #508); ``local`` rows are legacy
    job_dir-only names with no manifest metadata.
    """

    name: str
    storage: str = Field(description='"object" (authoritative manifest row) or "local"')
    node_key: str = ""
    size_bytes: int | None = None
    content_hash: str = ""
    uploaded_at: datetime | None = None
    media_type: str = Field(
        default="application/octet-stream",
        description=(
            "Content-Type the raw endpoint serves (whitelist-gated; JSON/text "
            "and non-whitelisted extensions download as octet-stream)"
        ),
    )


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
