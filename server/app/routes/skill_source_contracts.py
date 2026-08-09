from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class SkillSourceEntry(BaseModel):
    """Merged view of one skill: declared source + resolved lock entry."""

    model_config = ConfigDict(extra="forbid")

    key: str
    repo: str
    ref: str
    locked_commit: str | None
    resolved_at: str | None
    stale: bool


class SkillSourcesResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    skills: list[SkillSourceEntry]


class SkillSourceUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    repo: str = Field(min_length=1)
    ref: str = Field(min_length=1)
