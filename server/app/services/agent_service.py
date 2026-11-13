"""Read-only access to historical workspace-scoped Agent definitions.

Rows remain in ``versioned_entities`` for the legacy job/replay resolvers;
execution configuration for new work belongs to self-contained agent nodes
(EXEC-AGENT-PROFILE-001). Resolution never falls back to a global scope.
Hot read paths go through the short-TTL module cache below.
"""

from __future__ import annotations

import time

from server.app.agent_catalog import AgentDefinition
from server.app.db.connection import DatabaseDsn
from server.app.db.dialect import ConnectSource, resolve_dsn
from server.app.db.transaction import read_connection
from server.app.services.job_errors import InvalidOperationError
from server.app.services.versioned_entities import EntityType, VersionedEntity, VersionedEntityStore

_ENTITY_TYPE: EntityType = "agent"

PUBLISHED_CACHE_TTL_SECONDS = 5.0
_published_cache: dict[tuple[DatabaseDsn, str], tuple[float, dict[str, AgentDefinition]]] = {}


def reset_published_agent_cache() -> None:
    """Drop every cached catalog after direct historical-row writes or TRUNCATE."""
    _published_cache.clear()


def published_agent_definitions(
    connect_source: ConnectSource, workspace_id: str
) -> dict[str, AgentDefinition]:
    """Published Agent definitions of one workspace, keyed by agent_id, ~5s cache
    (``connect_source``: facade or DSN — BOUNDARY-DATA-001, #187)."""
    now = time.monotonic()
    cache_key = (resolve_dsn(connect_source), workspace_id)
    cached = _published_cache.get(cache_key)
    if cached is not None and now - cached[0] < PUBLISHED_CACHE_TTL_SECONDS:
        return cached[1]
    entities = VersionedEntityStore(connect_source, _ENTITY_TYPE).list_published(workspace_id)
    definitions = {e.entity_key: AgentDefinition.model_validate(e.definition) for e in entities}
    _published_cache[cache_key] = (now, definitions)
    return definitions


def has_published_agent_definitions(connect_source: ConnectSource) -> bool:
    """Cheap cross-workspace probe for poll-loop gates; never used for resolution."""
    with read_connection(resolve_dsn(connect_source)) as conn:
        row = conn.execute(
            "select exists(select 1 from versioned_entities where"
            " entity_type='agent' and status='published') as has_any"
        ).fetchone()
    return bool(row["has_any"]) if row is not None else False


class AgentService:
    """Read-only historical Agent definitions, bound to one workspace."""

    def __init__(self, connect_source: ConnectSource, workspace_id: str) -> None:
        if not workspace_id:
            raise InvalidOperationError("workspace id must be a non-empty string")
        self._store = VersionedEntityStore(connect_source, _ENTITY_TYPE)
        self._workspace_id = workspace_id

    def list_latest(self) -> list[VersionedEntity]:
        """Latest version per Agent (a pending draft beats the published row)."""
        return self._store.list_latest(self._workspace_id)

    def list_published(self) -> list[VersionedEntity]:
        """Published rows (latest rows can hide these; #460 P1)."""
        return self._store.list_published(self._workspace_id)

    def list_published_definitions(self) -> list[AgentDefinition]:
        return [
            AgentDefinition.model_validate(e.definition)
            for e in self._store.list_published(self._workspace_id)
        ]

    def get_published(self, agent_id: str) -> VersionedEntity | None:
        return self._store.get_published(agent_id, self._workspace_id)

    def get_published_definition(  # Optionally enforces an exact definition hash.
        self, agent_id: str, definition_hash: str | None = None
    ) -> AgentDefinition | None:
        entity = self._store.get_published(agent_id, self._workspace_id)
        if entity is None or (definition_hash and entity.definition_hash != definition_hash):
            return None
        return AgentDefinition.model_validate(entity.definition)

    def list_versions(self, agent_id: str) -> list[VersionedEntity]:
        return self._store.list_versions(agent_id, self._workspace_id)
