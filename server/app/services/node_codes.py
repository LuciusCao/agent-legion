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

import logging
from typing import Any

from server.app.db.dialect import ConnectSource
from server.app.services.job_errors import (
    ConflictError,
    CustomNodesDisabledError,
    InvalidOperationError,
    NotFoundError,
)
from server.app.services.node_code_validation import (
    DEFAULT_MAX_CODE_BYTES,
    _entity_key,
    _split_entity_key,
    code_hash,
    validate_node_code,
)
from server.app.services.versioned_entities import EntityType, VersionedEntity, VersionedEntityStore

logger = logging.getLogger(__name__)

_ENTITY_TYPE: EntityType = "node_code"


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


class NodeCodeService:
    """Versioned custom node code storage and publish flow.

    ``database_dsn`` accepts the JobQueries facade or a bare DSN string
    (BOUNDARY-DATA-001, #187); production wiring passes the facade.
    """

    def __init__(
        self,
        database_dsn: ConnectSource,
        custom_nodes_enabled: bool = True,
        max_code_bytes: int = DEFAULT_MAX_CODE_BYTES,
    ) -> None:
        self._store = VersionedEntityStore(database_dsn, _ENTITY_TYPE)
        self._enabled = custom_nodes_enabled
        self._max_code_bytes = max_code_bytes

    def _require_enabled(self) -> None:
        if not self._enabled:
            raise CustomNodesDisabledError("custom workflow nodes are disabled")

    def _check_publish_size(self, code: str, action: str) -> None:
        """#628 review P2: re-check the byte ceiling on the content being published.

        ``node_code_max_bytes`` is restart-effective: a draft or historical
        version saved under a larger budget would otherwise re-enter the
        effective code path (and the claim bundle) after the instance lowers
        the limit.
        """
        size = len(code.encode("utf-8"))
        if size > self._max_code_bytes:
            raise InvalidOperationError(
                f"cannot {action} node code of {size} bytes: it exceeds the current"
                f" {self._max_code_bytes}-byte node_code_max_bytes (default"
                f" {DEFAULT_MAX_CODE_BYTES}); the limit was lowered after this version was saved"
            )

    def _current_draft(self, workspace_id: str, workflow_key: str, node_key: str) -> dict[str, Any]:
        entity = self._store.get_draft(_entity_key(workflow_key, node_key), workspace_id)
        if entity is None:
            raise NotFoundError(
                f"no draft for {_ENTITY_TYPE} {_entity_key(workflow_key, node_key)}"
            )
        return _to_row(entity)

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
        validate_node_code(code, self._max_code_bytes)
        entity = self._store.save_draft(
            _entity_key(workflow_key, node_key),
            {"code": code, "change_note": change_note},
            code_hash(code),
            workspace_id,
            created_by,
        )
        return _to_row(entity)

    def publish(
        self,
        workspace_id: str,
        workflow_key: str,
        node_key: str,
        expected_hash: str | None = None,
    ) -> dict[str, Any]:
        """Publish the current draft; the previously published version archives.
        ``expected_hash`` (#692): verified atomically inside the store's
        publish transaction — mismatch raises Conflict with zero side
        effects.

        #628 review P2: the draft's bytes are re-validated against the current
        ``node_code_max_bytes`` BEFORE the publish — an oversized draft (saved
        under a since-lowered budget) is rejected up front, leaving the
        previous published version effective. The publish is then bound to
        exactly the validated bytes through the store's expected_hash CAS
        (#692): a concurrent draft overwrite between the validation read and
        the publish fails as Conflict instead of slipping unvalidated content
        through.
        """
        self._require_enabled()
        draft = self._current_draft(workspace_id, workflow_key, node_key)
        self._check_publish_size(str(draft["code"]), "publish")
        # #779 列车 R2 复审 P2-A：CAS 恒绑定已校验草稿的哈希。调用方携带
        # 的 expected_hash 只是乐观并发断言——与预读草稿不一致即刻拒为
        # Conflict；若改用它做 CAS，并发覆盖成调用方断言的（未校验）内容
        # 即可绕过上限复检。
        draft_hash = str(draft["code_hash"])
        if expected_hash is not None and expected_hash != draft_hash:
            raise ConflictError(
                f"draft hash mismatch for {_ENTITY_TYPE} {_entity_key(workflow_key, node_key)}:"
                " the draft was overwritten by another session; reload and retry"
            )
        row = _to_row(
            self._store.publish(_entity_key(workflow_key, node_key), workspace_id, draft_hash)
        )
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
        """Re-publish an old version as a new version (versions stay immutable).

        #628 review P2: the source version's bytes are re-validated against
        the current ``node_code_max_bytes`` before the rollback — versions are
        immutable, so the pre-read is race-free; a rejection leaves the
        currently published version untouched.
        """
        self._require_enabled()
        source = self.get_code_by_version(workspace_id, workflow_key, node_key, version)
        if source is None:
            raise NotFoundError(
                f"no version {version} for {_ENTITY_TYPE} {_entity_key(workflow_key, node_key)}"
            )
        self._check_publish_size(str(source["code"]), "rollback")
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
        validate_node_code(code, self._max_code_bytes)
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
