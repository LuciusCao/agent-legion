from __future__ import annotations

from pathlib import Path
from typing import Literal, cast

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from pydantic_core import InitErrorDetails


class LocalCapabilityConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    handler: str = Field(min_length=1)


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


class RemoteCapabilityConfig(BaseModel):
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


class RemoteExecutorConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    kind: Literal["remote"]
    payload: str = "pi"
    global_capacity: int = Field(gt=0, strict=True)
    capabilities: dict[str, RemoteCapabilityConfig]

    @field_validator("payload", mode="after")
    @classmethod
    def _reject_unknown_payload(cls, value: str) -> str:
        from server.app.executors import remote_payloads  # lazy: registry import cycle

        if not remote_payloads.has_payload_builder(value):
            raise ValueError(f"unknown executor payload {value!r}")
        return value

    @field_validator("capabilities", mode="after")
    @classmethod
    def _reject_empty_capability_names(
        cls, value: dict[str, RemoteCapabilityConfig]
    ) -> dict[str, RemoteCapabilityConfig]:
        if "" in value:
            raise ValueError("capability names must not be empty")
        return value


ExecutorConfig = (
    LocalExecutorConfig | PiExecutorConfig | OpenClawExecutorConfig | RemoteExecutorConfig
)
"""Union of the built-in executor config models, for type annotations only."""


def _validation_error_with_executor_id(exc: ValidationError, executor_id: str) -> ValidationError:
    line_errors = exc.errors(include_url=False)
    for error in line_errors:
        ctx = error.get("ctx") or {}
        ctx["executor_id"] = executor_id
        error["ctx"] = ctx
        error["loc"] = (executor_id, *error.get("loc", ()))
    return ValidationError.from_exception_data(exc.title, cast(list[InitErrorDetails], line_errors))


def load_executor_definitions(raw: dict[str, object]) -> dict[str, BaseModel]:
    """Validate a mapping of executor ID to executor configuration.

    Dispatch goes through the kind registry: unknown kinds raise
    ``UnknownExecutorKindError`` (message includes the executor ID); model
    validation errors are wrapped with the executor ID context.
    """
    # Lazy import: kinds.py pulls in runtime_config.py, which imports this
    # module; importing at module scope would create a cycle.
    from server.app.executors.kinds import load_executor_config

    definitions: dict[str, BaseModel] = {}
    for executor_id, value in raw.items():
        if not isinstance(value, dict):
            raise TypeError(
                f"Executor {executor_id!r}: expected a mapping, got {type(value).__name__}"
            )
        try:
            definitions[executor_id] = load_executor_config(executor_id, value)
        except ValidationError as exc:
            raise _validation_error_with_executor_id(exc, executor_id) from exc
    return definitions
