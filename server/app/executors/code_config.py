"""Config models for the code executor kind (split from config.py for size)."""

from __future__ import annotations

import logging
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
    timeout_seconds: int = Field(default=600, ge=1)
    # Custom (DB-backed) code for this capability runs inside the velites OS
    # sandbox (EXEC-CODE-003), which denies network by default; flip this on
    # for capabilities whose node must reach a service (e.g. the CMS).
    sandbox_network: bool = False
    # Non-secret tunable parameters for the node_config chain (spec D15);
    # secrets stay in resource bindings / the vault (spec D16).
    config_schema: dict[str, Any] = Field(default_factory=dict)

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


logger = logging.getLogger(__name__)

# The published-catalog cache (executor_definition_service, ~5s TTL) reparses
# stored definitions on every refresh, so an unstripped legacy definition
# would re-warn each cycle; warn once per executor per process instead.
_warned_path_strip: set[str] = set()


def _log_path_strip_warning(executor_id: str, retired: list[str]) -> None:
    if executor_id in _warned_path_strip:
        return
    _warned_path_strip.add(executor_id)
    logger.warning(
        "executor %r: dropped retired capability path key for %s "
        "(EXEC-CODE-001 legacy; the capability is custom-code-only now)",
        executor_id,
        ", ".join(sorted(retired)),
    )


def strip_retired_path_keys(executor_id: str, value: dict[str, object]) -> dict[str, object]:
    """Drop the retired capability ``path`` key (EXEC-CODE-001 legacy, #96).

    Stored definitions from before the path-mechanism retirement may still
    carry ``path``; versioned entities are immutable, so the key is stripped
    at parse time (the capability becomes custom-code-only: its code comes
    from a published node_code version — workspace-scoped, or the global
    factory seed for the demo nodes). Never written back to the DB.
    """
    if value.get("kind") != "code":
        return value
    capabilities = value.get("capabilities")
    if not isinstance(capabilities, dict):
        return value
    stripped: dict[str, object] = {}
    retired: list[str] = []
    for name, cap in capabilities.items():
        if isinstance(cap, dict) and "path" in cap:
            cap = {key: val for key, val in cap.items() if key != "path"}
            retired.append(str(name))
        stripped[str(name)] = cap
    if retired:
        _log_path_strip_warning(executor_id, retired)
        return {**value, "capabilities": stripped}
    return value
