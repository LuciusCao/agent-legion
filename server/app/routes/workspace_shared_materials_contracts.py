"""Contracts for the user-facing workspace shared-materials view (#643).

Read-only mirror of the studio-agent shared-materials surface for full user
sessions: the parsed map with a per-skill drift status folded into each
material entry, and a content-free file listing (single-file contents come
from the ``/file`` endpoint on demand).
"""

from typing import Literal

from pydantic import BaseModel, Field

DriftStatus = Literal["synced", "pending_sync", "missing_in_skill", "skill_not_found"]


class SharedMaterialSkillDrift(BaseModel):
    skill: str
    status: DriftStatus


class SharedMaterialMapping(BaseModel):
    source: str
    skills: list[SharedMaterialSkillDrift]


class SharedMaterialsMapView(BaseModel):
    version: int
    materials: list[SharedMaterialMapping] = Field(default_factory=list)


class SharedMaterialFileEntry(BaseModel):
    """Listing form — no content; the UI fetches single files on demand."""

    path: str
    size: int = Field(ge=0)
    modified_at: str


class WorkspaceSharedMaterialsResponse(BaseModel):
    """``map: null`` + empty ``files`` is the structured empty state for a
    workspace that never opted into ``_shared`` (same semantics as the
    studio-agent surface)."""

    workspace_id: str
    map: SharedMaterialsMapView | None = None
    files: list[SharedMaterialFileEntry] = Field(default_factory=list)


class SharedMaterialFileContent(BaseModel):
    path: str
    size: int = Field(ge=0)
    content: str
    truncated: bool = False
