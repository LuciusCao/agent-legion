"""Config models for the code executor kind (split from config.py for size)."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
)

from server.app.config_schema import validate_config_schema


class CodeCapabilityConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    # Repo-relative path to a tracked Python file exposing ``run(job, job_dir, runtime)``.
    path: str = Field(min_length=1)
    timeout_seconds: int = Field(default=600, ge=1)
    # Custom (DB-backed) code for this capability runs inside the velites OS
    # sandbox (EXEC-CODE-003), which denies network by default; flip this on
    # for capabilities whose node must reach a service (e.g. the CMS).
    sandbox_network: bool = False
    # Non-secret tunable parameters for the node_config chain (spec D15);
    # secrets stay in resource bindings / the vault (spec D16).
    config_schema: dict[str, Any] = Field(default_factory=dict)

    @field_validator("path", mode="after")
    @classmethod
    def _reject_unsafe_path(cls, value: str) -> str:
        if value.startswith("/"):
            raise ValueError("code path must not be absolute")
        if ".." in Path(value).parts:
            raise ValueError("code path must not contain '..'")
        return value

    @field_validator("config_schema", mode="after")
    @classmethod
    def _validate_config_schema(cls, value: dict[str, Any]) -> dict[str, Any]:
        validate_config_schema(value)
        return value


class CodeExecutorConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    kind: Literal["code"]
    global_capacity: int = Field(gt=0, strict=True)
    capabilities: dict[str, CodeCapabilityConfig]

    @field_validator("capabilities", mode="after")
    @classmethod
    def _reject_empty_capability_names(
        cls, value: dict[str, CodeCapabilityConfig]
    ) -> dict[str, CodeCapabilityConfig]:
        if "" in value:
            raise ValueError("capability names must not be empty")
        return value


def validate_code_config_paths(
    executor_definitions: Mapping[str, Any], repo_root: Path
) -> list[tuple[str, str]]:
    """Startup check: every code capability path must stay inside the repo root."""
    root = repo_root.resolve()
    problems: list[tuple[str, str]] = []
    for executor_id, definition in executor_definitions.items():
        if not isinstance(definition, CodeExecutorConfig):
            continue
        for capability, cap_config in definition.capabilities.items():
            resolved = (root / cap_config.path).resolve()
            if not resolved.is_relative_to(root) or not resolved.is_file():
                problems.append(
                    (
                        f"executors.{executor_id}.capabilities.{capability}.path",
                        "code path does not resolve to a file inside the repository root",
                    )
                )
    return problems
