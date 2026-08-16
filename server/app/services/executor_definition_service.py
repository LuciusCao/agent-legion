"""Executor definition service: DB-backed executor catalog (schema v30).

Executor definitions (the retired ``config/workflow.yaml`` executors section)
are global (``workspace_id`` NULL) versioned entities sharing the
draft → published → archived lifecycle engine with custom node codes and Agent
definitions. The definition payload is the raw executor config shape
(``kind``/``global_capacity``/``capabilities``) that
``load_executor_definitions`` parses into the typed ``ExecutorConfig`` models.

Runtime semantics: ``create_app`` seeds the built-in catalog when absent and
hydrates ``settings.executor_definitions`` from the published rows;
publish/rollback/archive then hot-reload the runtime registry in place
(``executor_registry_factory.reload_published_executors``), no restart
needed. The short-TTL module cache below only serves read paths that hit the
catalog outside startup (Studio display, executor catalog route).

Validation runs at ``save_draft``/``publish``/``rollback`` by fully parsing
the payload (kind dispatch + the ``config_schema`` contract). The legacy
capability ``path`` key (EXEC-CODE-001, retired in #96) is tolerated and
stripped at parse time by ``load_executor_definitions``.
"""

from __future__ import annotations

import hashlib
import json
import time
from typing import TYPE_CHECKING, Any, cast

from server.app.db.connection import DatabaseDsn
from server.app.executors.builtin_definitions import BUILTIN_EXECUTOR_DEFINITIONS
from server.app.executors.config import ExecutorConfig
from server.app.executors.definitions import load_executor_definitions
from server.app.services.job_errors import InvalidOperationError
from server.app.services.versioned_entities import (
    EntityType,
    VersionedEntity,
    VersionedEntityStore,
)

if TYPE_CHECKING:
    from server.app.settings import Settings

_ENTITY_TYPE: EntityType = "executor"

# Published catalog cache: executor definitions change rarely (operator
# publishes in Studio, effective on restart), so a stale entry at worst shows
# a seconds-old definition; writes through this service invalidate immediately.
PUBLISHED_CACHE_TTL_SECONDS = 5.0
_published_cache: dict[DatabaseDsn, tuple[float, dict[str, ExecutorConfig]]] = {}


def reset_published_executor_cache() -> None:
    """Drop every cached catalog (tests: TRUNCATE isolation bypasses invalidation)."""
    _published_cache.clear()


def _parse_definitions(raw: dict[str, Any]) -> dict[str, ExecutorConfig]:
    return cast(dict[str, ExecutorConfig], load_executor_definitions(raw))


def published_executor_definitions(database_dsn: DatabaseDsn) -> dict[str, ExecutorConfig]:
    """Published executor definitions keyed by executor_id, cached ~5s per DSN."""
    now = time.monotonic()
    cached = _published_cache.get(database_dsn)
    if cached is not None and now - cached[0] < PUBLISHED_CACHE_TTL_SECONDS:
        return cached[1]
    entities = VersionedEntityStore(database_dsn, _ENTITY_TYPE).list_published(None)
    definitions = _parse_definitions({entity.entity_key: entity.definition for entity in entities})
    _published_cache[database_dsn] = (now, definitions)
    return definitions


def _invalidate_published_cache(database_dsn: DatabaseDsn) -> None:
    _published_cache.pop(database_dsn, None)


def _definition_hash(definition: dict[str, Any]) -> str:
    canonical = json.dumps(definition, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class ExecutorDefinitionService:
    """Versioned executor definition storage and publish flow (global scope)."""

    def __init__(self, database_dsn: DatabaseDsn) -> None:
        self._store = VersionedEntityStore(database_dsn, _ENTITY_TYPE)

    def list_latest(self) -> list[VersionedEntity]:
        """Latest version per executor (a pending draft beats the published row)."""
        return self._store.list_latest(None)

    def list_published_definitions(self) -> dict[str, ExecutorConfig]:
        """Published definitions keyed by executor_id (short-TTL cached)."""
        return published_executor_definitions(self._store._dsn)

    def get_published(self, executor_id: str) -> VersionedEntity | None:
        return self._store.get_published(executor_id, None)

    def list_versions(self, executor_id: str) -> list[VersionedEntity]:
        return self._store.list_versions(executor_id, None)

    def save_draft(
        self, executor_id: str, definition: dict[str, Any], created_by: str
    ) -> VersionedEntity:
        """Create a draft version, overwriting the existing draft when present.

        The payload must fully parse as an executor definition: kind dispatch,
        path safety (no absolute paths / '..'), and the config_schema contract.
        """
        if not executor_id:
            raise InvalidOperationError("executor id must be a non-empty string")
        _parse_definitions({executor_id: definition})
        entity = self._store.save_draft(
            executor_id, definition, _definition_hash(definition), None, created_by
        )
        _invalidate_published_cache(self._store._dsn)
        return entity

    def publish(self, executor_id: str) -> VersionedEntity:
        """Publish the current draft; the previously published version archives."""
        entity = self._store.publish(executor_id, None)
        _invalidate_published_cache(self._store._dsn)
        return entity

    def rollback(self, executor_id: str, version: int, created_by: str) -> VersionedEntity:
        """Re-publish an old version as a new version (versions stay immutable)."""
        entity = self._store.rollback(executor_id, version, None, created_by)
        _invalidate_published_cache(self._store._dsn)
        return entity

    def archive_all(self, executor_id: str) -> int:
        """Archive every version; the executor stops resolving (no published row)."""
        archived = self._store.archive_all(executor_id, None)
        _invalidate_published_cache(self._store._dsn)
        return archived

    def copy(
        self, source_executor_id: str, new_executor_id: str, created_by: str
    ) -> VersionedEntity:
        """Copy the latest source definition into a new executor as draft v1."""
        if not new_executor_id:
            raise InvalidOperationError("executor id must be a non-empty string")
        entity = self._store.copy(source_executor_id, new_executor_id, None, created_by)
        _invalidate_published_cache(self._store._dsn)
        return entity


def seed_builtin_executor_definitions(service: ExecutorDefinitionService) -> list[str]:
    """Publish each built-in executor that has no versioned row yet.

    Seed-if-absent: an executor the admin already touched (published a new
    definition, or archived every version to disable it) is never overwritten
    or resurrected — only a completely absent entity key gets the factory
    definition. Returns the executor IDs seeded this run.
    """
    seeded: list[str] = []
    for executor_id, definition in BUILTIN_EXECUTOR_DEFINITIONS.items():
        if service.list_versions(executor_id):
            continue
        service.save_draft(executor_id, definition, created_by="system")
        service.publish(executor_id)
        seeded.append(executor_id)
    return seeded


def hydrate_executor_definitions(settings: Settings) -> None:
    """Seed-if-absent, then hydrate ``settings.executor_definitions`` from the DB.

    Runs once at startup (``create_app``, right after instance settings
    hydration); later publishes hot-reload the registry without a restart.
    Also seeds the demo workflow's global node_code versions (#96): the
    code-default executor's demo capabilities are custom-code-only, their
    factory code publishes from the git-reviewed workflow_nodes/ sources.
    """
    from server.app.services.demo_node_seed import seed_demo_node_codes

    service = ExecutorDefinitionService(settings.database_url)
    seed_builtin_executor_definitions(service)
    seed_demo_node_codes(settings)
    settings.executor_definitions = service.list_published_definitions()
