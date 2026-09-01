"""Contracts for the preview panel surfaces (issue #328).

Shared by the studio-agent tool endpoints (``studio_agent_preview_tools.py``)
and the human-facing routes (``preview_panels.py``) so both sides of the
STUDIO-AGENT-001 split speak the same shapes.
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class PreviewPanelVersionResponse(BaseModel):
    """One immutable version row of a workspace preview panel bundle."""

    id: str
    workspace_id: str | None
    entity_key: str
    version: int
    status: Literal["draft", "published", "archived"]
    html: str
    html_hash: str
    created_by: str
    change_note: str | None = None
    created_at: datetime
    published_at: datetime | None = None


class PreviewPanelStateResponse(BaseModel):
    """Published bundle plus pending draft; both null = built-in fallback."""

    published: PreviewPanelVersionResponse | None = None
    draft: PreviewPanelVersionResponse | None = None


class PreviewPanelPublishedResponse(BaseModel):
    """Job detail iframe host read: the published bundle, null = fallback."""

    published: PreviewPanelVersionResponse | None = None


class PreviewPanelDraftRequest(BaseModel):
    html: str = Field(min_length=1)
    change_note: str | None = None


class PreviewContextJobSummary(BaseModel):
    id: str
    status: str | None = None
    source_type: str | None = None
    source_id: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    artifacts: list[str] = []


class PreviewContextResponse(BaseModel):
    """Recent jobs + artifact inventory + bounded content samples."""

    workspace_id: str
    recent_jobs: list[PreviewContextJobSummary]
    selected_job: PreviewContextJobSummary | None = None
    samples: dict[str, str]
    sample_max_chars: int
    truncated: list[str]
