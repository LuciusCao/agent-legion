from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from server.app.config_schema import validate_config_schema


class AgentDefinition(BaseModel):
    """Trusted, immutable definition of one logical Agent implementation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    capability: str = Field(min_length=1)
    runtime: Literal["pi", "openclaw", "velites"]
    skill: str = Field(min_length=1)
    tools: tuple[str, ...] = ("read", "write", "bash")
    requires_labels: dict[str, str] = Field(default_factory=dict)
    config_schema: dict[str, Any] = Field(default_factory=dict)

    @field_validator("config_schema", mode="after")
    @classmethod
    def _validate_config_schema(cls, value: dict[str, Any]) -> dict[str, Any]:
        validate_config_schema(value)
        return value

    @field_validator("skill", mode="after")
    @classmethod
    def _reject_unsafe_skill_path(cls, value: str) -> str:
        path = Path(value)
        if path.is_absolute() or ".." in path.parts:
            raise ValueError("skill path must be relative and must not contain '..'")
        return value

    @field_validator("tools", mode="after")
    @classmethod
    def _reject_empty_tools(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(not tool for tool in value):
            raise ValueError("tool names must not be empty")
        return value

    def definition_hash(self) -> str:
        payload = json.dumps(
            self.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()
