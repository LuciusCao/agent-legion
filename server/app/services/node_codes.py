"""DB-backed workflow node codes (EXEC-CODE-002).

Node code is data, not a repo asset: versions live in the
``versioned_entities`` table (entity_type ``node_code``, schema v26), are
immutable, and take effect only through the publish flow
(draft → published → archived). At most one published version exists per
``(workspace, workflow, node)`` (partial unique index). Runtime code is
workspace-scoped. Historical global versions (``workspace_id`` NULL) remain
readable only for compatibility with old quality-replay pins; new demo seeds
are published into their target workspace.

The feature is gated by ``workflows.custom_nodes_enabled`` (default on in this
phase, design §7); every public entry point checks the gate before validating
and raises ``CustomNodesDisabledError`` when it is off.
"""

from __future__ import annotations

import ast
import hashlib
import logging
from typing import Any

from server.app.db.dialect import ConnectSource
from server.app.services.job_errors import (
    ConflictError,
    CustomNodesDisabledError,
    InvalidOperationError,
    NotFoundError,
)
from server.app.services.versioned_entities import EntityType, VersionedEntity, VersionedEntityStore

logger = logging.getLogger(__name__)

# Custom nodes stay single-file and cohesive; oversized code is rejected.
MAX_CODE_BYTES = 64 * 1024

_ENTITY_TYPE: EntityType = "node_code"
_ENTITY_KEY_SEPARATOR = ":"


def _entity_key(workflow_key: str, node_key: str) -> str:
    if _ENTITY_KEY_SEPARATOR in workflow_key:
        raise InvalidOperationError(
            f"workflow key must not contain {_ENTITY_KEY_SEPARATOR!r}: {workflow_key}"
        )
    return f"{workflow_key}{_ENTITY_KEY_SEPARATOR}{node_key}"


def _split_entity_key(entity_key: str) -> tuple[str, str]:
    workflow_key, _, node_key = entity_key.partition(_ENTITY_KEY_SEPARATOR)
    return workflow_key, node_key


# Process-local publish generation (issue #124): the workflow worker's
# per-pass dispatch memo tags entries with this counter, so an in-process
# publish/rollback/archive invalidates memoized code on the very next claim
# (the #115 "next node execution" contract) instead of the next pass; the
# per-pass clear remains the backstop for writes from another process.
# Bumped only AFTER the store mutation commits.
_publish_generation = 0


def node_code_publish_generation() -> int:
    """Current node-code publish generation (monotonic within this process)."""
    return _publish_generation


def _bump_publish_generation() -> None:
    global _publish_generation
    _publish_generation += 1


def _to_row(entity: VersionedEntity) -> dict[str, Any]:
    """Rebuild the historical node-code row shape from a versioned entity."""
    workflow_key, node_key = _split_entity_key(entity.entity_key)
    return {
        "id": entity.id,
        "workspace_id": entity.workspace_id,
        "workflow_key": workflow_key,
        "node_key": node_key,
        "version": entity.version,
        "status": entity.status,
        "code": entity.definition["code"],
        "code_hash": entity.definition_hash,
        "created_by": entity.created_by,
        "change_note": entity.definition.get("change_note"),
        "created_at": entity.created_at,
        "published_at": entity.published_at,
    }


def validate_node_code(code: str) -> None:
    """Syntax + module-level ``run`` + size contract for custom node code."""
    if len(code.encode("utf-8")) > MAX_CODE_BYTES:
        raise InvalidOperationError(f"node code exceeds the {MAX_CODE_BYTES}-byte size limit")
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        raise InvalidOperationError(f"node code is not valid Python: {exc}") from exc
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "run":
            return
    raise InvalidOperationError("node code must define a module-level 'run' function")


def code_hash(code: str) -> str:
    return hashlib.sha256(code.encode("utf-8")).hexdigest()


class NodeCodeService:
    """Versioned custom node code storage and publish flow.

    ``database_dsn`` accepts the JobQueries facade or a bare DSN string
    (BOUNDARY-DATA-001, #187); production wiring passes the facade.
    """

    def __init__(self, database_dsn: ConnectSource, custom_nodes_enabled: bool = True) -> None:
        self._store = VersionedEntityStore(database_dsn, _ENTITY_TYPE)
        self._enabled = custom_nodes_enabled

    def _require_enabled(self) -> None:
        if not self._enabled:
            raise CustomNodesDisabledError("custom workflow nodes are disabled")

    def get_effective_code(
        self, workspace_id: str, workflow_key: str, node_key: str
    ) -> dict[str, Any] | None:
        """Return the workspace's published version row, or None."""
        self._require_enabled()
        entity = self._store.get_published(_entity_key(workflow_key, node_key), workspace_id)
        return _to_row(entity) if entity else None

    def get_global_published(self, workflow_key: str, node_key: str) -> dict[str, Any] | None:
        """Return a legacy global published row, or None (migration only)."""
        self._require_enabled()
        entity = self._store.get_published(_entity_key(workflow_key, node_key), None)
        return _to_row(entity) if entity else None

    def get_code_by_version(
        self, workspace_id: str | None, workflow_key: str, node_key: str, version: int
    ) -> dict[str, Any] | None:
        """Return any version row (including archived) — frozen jobs read these."""
        self._require_enabled()
        entity = self._store.get_version(_entity_key(workflow_key, node_key), version, workspace_id)
        return _to_row(entity) if entity else None

    def get_global_code_by_version(
        self, workflow_key: str, node_key: str, version: int
    ) -> dict[str, Any] | None:
        """Return the global (workspace-NULL) row at *version*, or None."""
        return self.get_code_by_version(None, workflow_key, node_key, version)

    def list_versions(
        self, workspace_id: str, workflow_key: str, node_key: str
    ) -> list[dict[str, Any]]:
        self._require_enabled()
        return [
            _to_row(entity)
            for entity in self._store.list_versions(
                _entity_key(workflow_key, node_key), workspace_id
            )
        ]

    def save_draft(
        self,
        workspace_id: str,
        workflow_key: str,
        node_key: str,
        code: str,
        created_by: str,
        change_note: str | None = None,
    ) -> dict[str, Any]:
        """Create a draft version, overwriting the existing draft when present."""
        self._require_enabled()
        validate_node_code(code)
        entity = self._store.save_draft(
            _entity_key(workflow_key, node_key),
            {"code": code, "change_note": change_note},
            code_hash(code),
            workspace_id,
            created_by,
        )
        return _to_row(entity)

    def publish(self, workspace_id: str, workflow_key: str, node_key: str) -> dict[str, Any]:
        """Publish the current draft; the previously published version archives."""
        self._require_enabled()
        row = _to_row(self._store.publish(_entity_key(workflow_key, node_key), workspace_id))
        _bump_publish_generation()
        return row

    def rollback(
        self,
        workspace_id: str,
        workflow_key: str,
        node_key: str,
        version: int,
        created_by: str,
        change_note: str | None = None,
    ) -> dict[str, Any]:
        """Re-publish an old version as a new version (versions stay immutable)."""
        self._require_enabled()
        entity = self._store.rollback(
            _entity_key(workflow_key, node_key),
            version,
            workspace_id,
            created_by,
            definition_patch={
                "change_note": change_note if change_note is not None else f"rollback to v{version}"
            },
        )
        _bump_publish_generation()
        return _to_row(entity)

    def archive_all(self, workspace_id: str | None, workflow_key: str, node_key: str) -> int:
        """Archive every version in one workspace or a legacy global scope."""
        self._require_enabled()
        archived = self._store.archive_all(_entity_key(workflow_key, node_key), workspace_id)
        if archived:
            _bump_publish_generation()
        return archived

    def seed_global(self, workflow_key: str, node_key: str, code: str, change_note: str) -> bool:
        """Publish *code* as the global (workspace-NULL) version when absent.

        Seed-if-absent: a
        global entity the operator somehow already touched is never
        overwritten. Returns True when a version was published this call.
        """
        self._require_enabled()
        entity_key = _entity_key(workflow_key, node_key)
        if self._store.list_versions(entity_key, None):
            return False
        validate_node_code(code)
        try:
            self._store.save_draft(
                entity_key,
                {"code": code, "change_note": change_note},
                code_hash(code),
                None,
                "system",
            )
            self._store.publish(entity_key, None)
        except (ConflictError, NotFoundError):
            # Startup race: a second Host process passed the emptiness check
            # concurrently and won the write. The entity is seeded either
            # way, so treat the conflict as "already seeded".
            # Residual window: a loser whose save_draft lands only after the
            # winner's draft+publish committed allocates v2 and publishing it
            # archives the winner's v1. Accepted as harmless — concurrent
            # seeds carry identical factory content and the window exists
            # only on first startup of an un-seeded database.
            return False
        _bump_publish_generation()
        return True
