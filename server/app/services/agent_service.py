"""Agent definition service: DB-backed Agent catalog (schema v26).

Agent definitions are global (workspace_id NULL) versioned entities sharing
the draft → published → archived lifecycle engine with custom node codes.
The definition payload is the pure ``AgentDefinition`` shape
(capability/runtime/skill/tools/config_schema) — execution configuration
(provider/model/thinking) deliberately lives outside it, resolved per node
with workspace defaults (see docs/architecture/agent-config-governance.md).

During the transition the YAML-synced ``agent_definitions`` table remains the
execution read path; this service becomes the source of truth when the YAML
catalog retires (phase 3).
"""

from __future__ import annotations

from server.app.agent_catalog import AgentDefinition
from server.app.db.connection import DatabaseDsn
from server.app.services.job_errors import ConflictError, InvalidOperationError
from server.app.services.versioned_entities import EntityType, VersionedEntity, VersionedEntityStore

_ENTITY_TYPE: EntityType = "agent"


class AgentService:
    """Versioned Agent definition storage and publish flow (global scope)."""

    def __init__(self, database_dsn: DatabaseDsn) -> None:
        self._store = VersionedEntityStore(database_dsn, _ENTITY_TYPE)

    def list_latest(self) -> list[VersionedEntity]:
        """Latest version per Agent (a pending draft beats the published row)."""
        return self._store.list_latest(None)

    def list_published_definitions(self) -> list[AgentDefinition]:
        return [
            AgentDefinition.model_validate(e.definition) for e in self._store.list_published(None)
        ]

    def get_published(self, agent_id: str) -> VersionedEntity | None:
        return self._store.get_published(agent_id, None)

    def get_published_definition(
        self, agent_id: str, definition_hash: str | None = None
    ) -> AgentDefinition | None:
        """Read the published definition, optionally enforcing an exact hash."""
        entity = self._store.get_published(agent_id, None)
        if entity is None or (
            definition_hash is not None and entity.definition_hash != definition_hash
        ):
            return None
        return AgentDefinition.model_validate(entity.definition)

    def list_versions(self, agent_id: str) -> list[VersionedEntity]:
        return self._store.list_versions(agent_id, None)

    def save_draft(
        self, agent_id: str, definition: AgentDefinition, created_by: str
    ) -> VersionedEntity:
        """Create a draft version, overwriting the existing draft when present."""
        if not agent_id:
            raise InvalidOperationError("agent id must be a non-empty string")
        return self._store.save_draft(
            agent_id,
            definition.model_dump(mode="json"),
            definition.definition_hash(),
            None,
            created_by,
        )

    def publish(self, agent_id: str) -> VersionedEntity:
        """Publish the current draft; the previously published version archives.

        Exactly one published Agent per capability: workspace routes derive
        from the capability alone, so two published definitions sharing one
        would make routing ambiguous (mirrors the YAML catalog constraint).
        """
        versions = self._store.list_versions(agent_id, None)
        draft = next((v for v in versions if v.status == "draft"), None)
        if draft is None:
            # Let the store raise the canonical NotFoundError.
            return self._store.publish(agent_id, None)
        capability = draft.definition.get("capability")
        for other in self._store.list_published(None):
            if other.entity_key != agent_id and other.definition.get("capability") == capability:
                raise ConflictError(
                    f"capability {capability!r} is already published by Agent"
                    f" {other.entity_key!r}; exactly one published Agent per capability"
                )
        return self._store.publish(agent_id, None)

    def rollback(self, agent_id: str, version: int, created_by: str) -> VersionedEntity:
        """Re-publish an old version as a new version (versions stay immutable)."""
        return self._store.rollback(agent_id, version, None, created_by)

    def archive_all(self, agent_id: str) -> int:
        """Archive every version; the Agent stops routing (no published row)."""
        return self._store.archive_all(agent_id, None)

    def copy(self, source_agent_id: str, new_agent_id: str, created_by: str) -> VersionedEntity:
        """Copy the latest source definition into a new Agent as draft v1."""
        if not new_agent_id:
            raise InvalidOperationError("agent id must be a non-empty string")
        return self._store.copy(source_agent_id, new_agent_id, None, created_by)
