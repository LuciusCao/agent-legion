from __future__ import annotations

from pydantic import BaseModel, Field


class SkillSourceConfig(BaseModel):
    repo: str
    ref: str


class SkillsConfig(BaseModel):
    skills: dict[str, SkillSourceConfig] = Field(default_factory=dict)


class LockedSkillSource(BaseModel):
    repo: str
    ref: str
    commit: str


class SkillsLock(BaseModel):
    version: str = "1"
    resolved_at: str | None = None
    skills: dict[str, LockedSkillSource] = Field(default_factory=dict)
