"""Agent definition service: DB-backed workspace-scoped Agent catalog (v46).

Workspace-scoped versioned entities sharing the draft → published → archived
lifecycle engine with custom node codes; the v46 migration deleted the global
rows, so resolution never falls back to a global scope. Execution
configuration (provider/model/thinking) resolves per node with workspace
defaults (docs/architecture/agent-config-governance.md). Hot read paths go
through the short-TTL module cache below.
"""

from __future__ import annotations

import time

from server.app.agent_catalog import AgentDefinition
from server.app.db.connection import DatabaseDsn
from server.app.db.transaction import read_connection
from server.app.services.job_errors import ConflictError, InvalidOperationError
from server.app.services.versioned_entities import EntityType, VersionedEntity, VersionedEntityStore

_ENTITY_TYPE: EntityType = "agent"

# Published catalog cache keyed by (DSN, workspace): definitions change rarely
# but the claim path reads them per candidate; publishes/archives invalidate.
PUBLISHED_CACHE_TTL_SECONDS = 5.0
_published_cache: dict[tuple[DatabaseDsn, str], tuple[float, dict[str, AgentDefinition]]] = {}


def reset_published_agent_cache() -> None:
    """Drop every cached catalog (tests: TRUNCATE isolation bypasses invalidation)."""
    _published_cache.clear()


def published_agent_definitions(
    database_dsn: DatabaseDsn, workspace_id: str
) -> dict[str, AgentDefinition]:
    """Published Agent definitions of one workspace, keyed by agent_id, cached ~5s."""
    now = time.monotonic()
    cache_key = (database_dsn, workspace_id)
    cached = _published_cache.get(cache_key)
    if cached is not None and now - cached[0] < PUBLISHED_CACHE_TTL_SECONDS:
        return cached[1]
    entities = VersionedEntityStore(database_dsn, _ENTITY_TYPE).list_published(workspace_id)
    definitions = {
        entity.entity_key: AgentDefinition.model_validate(entity.definition) for entity in entities
    }
    _published_cache[cache_key] = (now, definitions)
    return definitions


def has_published_agent_definitions(database_dsn: DatabaseDsn) -> bool:
    """Cheap cross-workspace probe for poll-loop gates; never used for resolution."""
    with read_connection(database_dsn) as conn:
        row = conn.execute(
            "select exists(select 1 from versioned_entities"
            " where entity_type='agent' and status='published') as has_any"
        ).fetchone()
    return bool(row["has_any"]) if row is not None else False


def _invalidate_published_cache(database_dsn: DatabaseDsn, workspace_id: str) -> None:
    _published_cache.pop((database_dsn, workspace_id), None)


class AgentService:
    """Versioned Agent definition storage/publish flow, bound to one workspace."""

    def __init__(self, database_dsn: DatabaseDsn, workspace_id: str) -> None:
        if not workspace_id:
            raise InvalidOperationError("workspace id must be a non-empty string")
        self._store = VersionedEntityStore(database_dsn, _ENTITY_TYPE)
        self._workspace_id = workspace_id

    def list_latest(self) -> list[VersionedEntity]:
        """Latest version per Agent (a pending draft beats the published row)."""
        return self._store.list_latest(self._workspace_id)

    def list_published_definitions(self) -> list[AgentDefinition]:
        return [
            AgentDefinition.model_validate(e.definition)
            for e in self._store.list_published(self._workspace_id)
        ]

    def get_published(self, agent_id: str) -> VersionedEntity | None:
        return self._store.get_published(agent_id, self._workspace_id)

    def get_published_definition(
        self, agent_id: str, definition_hash: str | None = None
    ) -> AgentDefinition | None:
        """Read the published definition, optionally enforcing an exact hash."""
        entity = self._store.get_published(agent_id, self._workspace_id)
        if entity is None or (
            definition_hash is not None and entity.definition_hash != definition_hash
        ):
            return None
        return AgentDefinition.model_validate(entity.definition)

    def list_versions(self, agent_id: str) -> list[VersionedEntity]:
        return self._store.list_versions(agent_id, self._workspace_id)

    def save_draft(
        self, agent_id: str, definition: AgentDefinition, created_by: str
    ) -> VersionedEntity:
        """Create a draft version, overwriting the existing draft when present."""
        if not agent_id:
            raise InvalidOperationError("agent id must be a non-empty string")
        entity = self._store.save_draft(
            agent_id,
            definition.model_dump(mode="json"),
            definition.definition_hash(),
            self._workspace_id,
            created_by,
        )
        _invalidate_published_cache(self._store._dsn, self._workspace_id)
        return entity

    def publish(self, agent_id: str) -> VersionedEntity:
        """Publish the current draft; the previously published version archives.

        Exactly one published Agent per capability per workspace — routes
        derive from the capability alone (mirrors the YAML catalog constraint).
        """
        versions = self._store.list_versions(agent_id, self._workspace_id)
        draft = next((v for v in versions if v.status == "draft"), None)
        if draft is not None:
            self._require_free_capability(agent_id, str(draft.definition.get("capability") or ""))
        # draft None → the store raises the canonical NotFoundError.
        entity = self._store.publish(agent_id, self._workspace_id)
        _invalidate_published_cache(self._store._dsn, self._workspace_id)
        return entity

    def _require_free_capability(self, agent_id: str, capability: str) -> None:
        """Service-layer capability conflict check; the DB partial unique index
        ``versioned_entities_published_capability`` is the real guard."""
        for other in self._store.list_published(self._workspace_id):
            if other.entity_key != agent_id and other.definition.get("capability") == capability:
                raise ConflictError(
                    f"capability {capability!r} is already published by Agent"
                    f" {other.entity_key!r} in this workspace;"
                    " exactly one published Agent per capability"
                )

    def rollback(self, agent_id: str, version: int, created_by: str) -> VersionedEntity:
        """Re-publish an old version as a new version (versions stay immutable)."""
        versions = self._store.list_versions(agent_id, self._workspace_id)
        source = next((v for v in versions if v.version == version), None)
        if source is not None:
            self._require_free_capability(agent_id, str(source.definition.get("capability") or ""))
        entity = self._store.rollback(agent_id, version, self._workspace_id, created_by)
        _invalidate_published_cache(self._store._dsn, self._workspace_id)
        return entity

    def archive_all(self, agent_id: str) -> int:
        """Archive every version; the Agent stops routing (no published row)."""
        archived = self._store.archive_all(agent_id, self._workspace_id)
        _invalidate_published_cache(self._store._dsn, self._workspace_id)
        return archived

    def copy(self, source_agent_id: str, new_agent_id: str, created_by: str) -> VersionedEntity:
        """Copy the latest source definition into a new Agent as draft v1."""
        if not new_agent_id:
            raise InvalidOperationError("agent id must be a non-empty string")
        entity = self._store.copy(source_agent_id, new_agent_id, self._workspace_id, created_by)
        _invalidate_published_cache(self._store._dsn, self._workspace_id)
        return entity
