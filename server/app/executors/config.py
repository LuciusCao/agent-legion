from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
)

from server.app.executors.code_config import (  # noqa: F401  (re-export)
    CodeCapabilityConfig,
    CodeExecutorConfig,
)


class PiCapabilityConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    skill: str = Field(min_length=1)
    tools: tuple[str, ...] = ("read", "write", "bash")

    @field_validator("skill", mode="after")
    @classmethod
    def _reject_unsafe_skill_path(cls, value: str) -> str:
        if value.startswith("/"):
            raise ValueError("skill path must not be absolute")
        if ".." in Path(value).parts:
            raise ValueError("skill path must not contain '..'")
        return value


class OpenClawCapabilityConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    skill: str = Field(min_length=1)


class PiExecutorConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    kind: Literal["pi"]
    global_capacity: int = Field(gt=0, strict=True)
    capabilities: dict[str, PiCapabilityConfig]

    @field_validator("capabilities", mode="after")
    @classmethod
    def _reject_empty_capability_names(
        cls, value: dict[str, PiCapabilityConfig]
    ) -> dict[str, PiCapabilityConfig]:
        if "" in value:
            raise ValueError("capability names must not be empty")
        return value


class OpenClawExecutorConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    kind: Literal["openclaw"]
    agent_id: str = Field(min_length=1)
    global_capacity: int = Field(gt=0, strict=True)
    capabilities: dict[str, OpenClawCapabilityConfig]

    @field_validator("capabilities", mode="after")
    @classmethod
    def _reject_empty_capability_names(
        cls, value: dict[str, OpenClawCapabilityConfig]
    ) -> dict[str, OpenClawCapabilityConfig]:
        if "" in value:
            raise ValueError("capability names must not be empty")
        return value


ExecutorConfig = CodeExecutorConfig | PiExecutorConfig | OpenClawExecutorConfig
"""Union of the built-in executor config models, for type annotations only."""
