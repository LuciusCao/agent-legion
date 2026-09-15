"""Contracts for shared-material propagation (issue #673).

The write counterpart of the shared-materials view: propagate selected
(or all) map entries into each mapped skill repo — commit + new patch
tag per skill, results isolated per skill.
"""

from typing import Literal

from pydantic import BaseModel, Field

PropagateStatus = Literal["synced", "skipped", "failed"]


class SharedMaterialsPropagateRequest(BaseModel):
    """``sources: null`` (or omitted) propagates every mapped entry."""

    sources: list[str] | None = None


class SharedMaterialPropagateSkillResult(BaseModel):
    skill: str
    status: PropagateStatus
    tag: str | None = None
    detail: str | None = None
    synced_files: list[str] = Field(default_factory=list)


class SharedMaterialsPropagateResponse(BaseModel):
    workspace_id: str
    results: list[SharedMaterialPropagateSkillResult] = Field(default_factory=list)
