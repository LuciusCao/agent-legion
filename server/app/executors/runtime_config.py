from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class PiRuntimeConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    binary: str = "pi"
    provider: str = ""
    model: str = ""
    thinking: str = ""
    timeout_seconds: int = Field(default=600, ge=1)
    cancellation_grace_seconds: int = Field(default=5, ge=0)
    environment: dict[str, str] = Field(default_factory=dict)


class OpenClawSkillSafetyRuntimeConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    repos: list[dict[str, str]] = Field(default_factory=list)


class OpenClawRuntimeConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    command_template: tuple[str, ...] = Field(min_length=1)
    cwd: str = "."
    timeout_seconds: int = Field(default=600, ge=1)
    cancellation_grace_seconds: int = Field(default=5, ge=0)
    isolated_workspace_root: str = ""
    skill_safety: OpenClawSkillSafetyRuntimeConfig = Field(
        default_factory=OpenClawSkillSafetyRuntimeConfig
    )


class PipelinesRuntimeConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    pi: PiRuntimeConfig = Field(default_factory=PiRuntimeConfig)


class ExecutorRuntimeConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    cancellation_grace_seconds: int = Field(default=5, ge=0)
    pipelines: PipelinesRuntimeConfig = Field(default_factory=PipelinesRuntimeConfig)
    openclaw: OpenClawRuntimeConfig = Field(
        default_factory=lambda: OpenClawRuntimeConfig(command_template=("openclaw",))
    )
