"""Admin API contract for the OpenClaw block of the instance settings document.

The block carries the values retired from the ``config/agent_legion.yaml``
``openclaw:`` section. Skill-safety repos are a path-only allowlist: restore
refs are pinned by ``config/skills.lock`` (config governance G3), so a ``ref``
key is rejected as an extra field (422).
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class InstanceOpenClawSkillSafetyRepo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(min_length=1)


class InstanceOpenClawSkillSafetySettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool
    repos: list[InstanceOpenClawSkillSafetyRepo]


class InstanceOpenClawSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cwd: str
    timeout_seconds: int = Field(ge=1)
    isolated_workspace_root: str
    command_template: list[str] = Field(min_length=1)
    skill_safety: InstanceOpenClawSkillSafetySettings
