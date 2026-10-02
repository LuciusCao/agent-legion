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

    #739: object-backed rows additionally carry a presigned GET
    ``download_url`` (S3 answers it directly — big media downloads leave the
    Host process alone). The URL serves the raw endpoint's representation:
    its response headers (Content-Type, attachment disposition and, for
    ``.gz`` rows (#338), ``Content-Encoding: gzip``) are signed in, so the
    two channels answer alike. It addresses the CURRENT bytes under the name
    (rerun semantics #508, same as raw — verify against ``content_hash``) and
    ``expires_at`` is an upper bound (re-fetch the manifest on 403).
    ``local`` rows and instances without object storage keep both fields null.
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
    download_url: str | None = Field(
        default=None,
        description=(
            "Presigned object-storage GET URL answering with the raw endpoint's "
            "headers (storage=object rows); null for local entries and instances "
            "without object storage — use the raw endpoint then"
        ),
    )
    expires_at: datetime | None = Field(
        default=None,
        description=(
            "Latest moment download_url can work (upper bound: it may 403 "
            "earlier, e.g. short-lived signing credentials — re-fetch the "
            "manifest then); null whenever download_url is null"
        ),
    )
    content_encoding: str = Field(
        default="",
        description=(
            'Stored-form marker: "gzip" when the object holds gzip-compressed '
            "bytes (#338) — both download_url and the raw endpoint answer with "
            "Content-Encoding: gzip (HTTP clients decode transparently); empty "
            "otherwise"
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
