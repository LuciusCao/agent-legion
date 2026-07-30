from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
)

from server.app.config_schema import validate_config_schema


class LocalCapabilityConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    handler: str = Field(min_length=1)
    # Wall-clock limit for one isolated run of this capability; absent falls
    # back to the executor default (local.DEFAULT_TIMEOUT_SECONDS).
    timeout_seconds: float | None = Field(default=None, gt=0)
    # Non-secret tunable parameters for the node_config chain (spec D15);
    # secrets stay in resource bindings / the vault (spec D16).
    config_schema: dict[str, Any] = Field(default_factory=dict)

    @field_validator("config_schema", mode="after")
    @classmethod
    def _validate_config_schema(cls, value: dict[str, Any]) -> dict[str, Any]:
        validate_config_schema(value)
        return value


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


class LocalExecutorConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    kind: Literal["local"]
    global_capacity: int = Field(gt=0, strict=True)
    capabilities: dict[str, LocalCapabilityConfig]

    @field_validator("capabilities", mode="after")
    @classmethod
    def _reject_empty_capability_names(
        cls, value: dict[str, LocalCapabilityConfig]
    ) -> dict[str, LocalCapabilityConfig]:
        if "" in value:
            raise ValueError("capability names must not be empty")
        return value


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


ExecutorConfig = LocalExecutorConfig | PiExecutorConfig | OpenClawExecutorConfig
"""Union of the built-in executor config models, for type annotations only."""
