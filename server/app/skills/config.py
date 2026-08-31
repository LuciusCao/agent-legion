from __future__ import annotations

from pydantic import BaseModel, Field, model_validator


class SkillSourceConfig(BaseModel):
    repo: str
    ref: str


class SkillsConfig(BaseModel):
    skills: dict[str, SkillSourceConfig] = Field(default_factory=dict)


class LockedSkill(BaseModel):
    repo: str
    # ref -> commit: every ref ever pinned for this skill stays frozen (issue
    # #76); dispatch resolves the commit for the ref it was asked for.
    refs: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def _upgrade_v1(cls, data: object) -> object:
        # v1 lock entries {repo, ref, commit} upgrade to refs={ref: commit}.
        if isinstance(data, dict) and "refs" not in data:
            ref, commit = data.get("ref"), data.get("commit")
            return {"repo": data.get("repo", ""), "refs": {ref: commit} if ref and commit else {}}
        return data


class SkillsLock(BaseModel):
    version: str = "2"
    resolved_at: str | None = None
    skills: dict[str, LockedSkill] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _stamp_v2(self) -> SkillsLock:
        # Entries self-upgrade to the multi-ref shape above, so any validated
        # document is v2 regardless of the version label it was stored with.
        self.version = "2"
        return self
