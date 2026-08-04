from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from server.app.config_schema import validate_config_schema
from server.app.db.connection import DatabaseDsn
from server.app.db.transaction import read_connection, write_transaction


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


def load_agent_definitions(raw: Any) -> dict[str, AgentDefinition]:
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise TypeError("agents must be a mapping")
    definitions: dict[str, AgentDefinition] = {}
    for agent_id, value in raw.items():
        if not isinstance(agent_id, str) or not agent_id:
            raise ValueError("agent IDs must be non-empty strings")
        definitions[agent_id] = AgentDefinition.model_validate(value)
    _enforce_unique_capabilities(definitions)
    return definitions


def _enforce_unique_capabilities(definitions: Mapping[str, AgentDefinition]) -> None:
    """Phase 1 constraint: exactly one Agent Definition per capability.

    Workspace Routes are derived from the capability alone, so two enabled
    definitions sharing a capability would make routing ambiguous. Explicit
    route selection is out of scope for phase 1; keep one definition per
    capability (disable or remove the other) instead.
    """
    owner_by_capability: dict[str, str] = {}
    for agent_id, definition in definitions.items():
        existing = owner_by_capability.setdefault(definition.capability, agent_id)
        if existing != agent_id:
            raise ValueError(
                f"capability {definition.capability!r} is declared by multiple Agent"
                f" Definitions ({existing!r}, {agent_id!r}); phase 1 requires exactly"
                " one Agent Definition per capability — disable or remove duplicates"
            )


def sync_agent_definitions(
    database_dsn: DatabaseDsn,
    definitions: Mapping[str, AgentDefinition],
) -> None:
    """Persist the configured Agent Catalog as an immutable execution snapshot source.

    Fail-fast guard: an empty mapping combined with already-enabled rows means the
    `agents:` configuration section regressed (wrong file, bad merge, failed
    load). Disabling every Agent silently would cascade into route pruning and
    fall back to legacy executor bindings, so refuse the sync instead.
    """
    with write_transaction(database_dsn) as conn:
        if not definitions:
            enabled_row = conn.execute(
                "select count(*) as c from agent_definitions where enabled=1"
            ).fetchone()
            enabled_count = int(enabled_row["c"]) if enabled_row is not None else 0
            if enabled_count:
                raise ValueError(
                    f"empty Agent catalog would disable {enabled_count} enabled Agent"
                    " Definition(s); refusing to sync — check the `agents:`"
                    " configuration section"
                )
        conn.execute("update agent_definitions set enabled=0, updated_at=current_timestamp")
        for agent_id, definition in definitions.items():
            definition_json = json.dumps(
                definition.model_dump(mode="json"),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            conn.execute(
                """
                insert into agent_definitions(
                  agent_id, capability, runtime, definition_json,
                  definition_hash, enabled, updated_at
                ) values (?, ?, ?, ?, ?, 1, current_timestamp)
                on conflict(agent_id) do update set
                  capability=excluded.capability,
                  runtime=excluded.runtime,
                  definition_json=excluded.definition_json,
                  definition_hash=excluded.definition_hash,
                  enabled=1,
                  updated_at=current_timestamp
                """,
                (
                    agent_id,
                    definition.capability,
                    definition.runtime,
                    definition_json,
                    definition.definition_hash(),
                ),
            )


def get_agent_definition(
    database_dsn: DatabaseDsn,
    agent_id: str,
    definition_hash: str | None = None,
) -> AgentDefinition | None:
    """Read the current catalog definition, optionally enforcing an exact hash."""
    with read_connection(database_dsn) as conn:
        row = conn.execute(
            "select definition_json, definition_hash from agent_definitions"
            " where agent_id=? and enabled=1",
            (agent_id,),
        ).fetchone()
    if row is None or (definition_hash is not None and row["definition_hash"] != definition_hash):
        return None
    return AgentDefinition.model_validate_json(row["definition_json"])
