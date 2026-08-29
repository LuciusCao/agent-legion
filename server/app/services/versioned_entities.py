"""Unified versioned entity storage (schema v26).

One table (``versioned_entities``) backs the draft → published → archived
lifecycle of every versioned definition: custom node codes (``node_code``),
Agent definitions (``agent``), and retired executor definitions
(``executor``). New entities are workspace-scoped; ``workspace_id`` is NULL
only for historical global rows retained for migration/replay compatibility.
Versions are immutable:
publishing archives the previously published row (the partial unique index
guarantees at most one published row per entity), and rollback re-publishes
an old definition as a new version.

``VersionedEntityStore`` is the shared lifecycle engine; per-type services
(``NodeCodeService``, ``AgentService``) compose it and own their definition
payload shape and validation.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from psycopg import IntegrityError

from server.app.db.dialect import ConnectSource
from server.app.db.transaction import read_connection, write_transaction
from server.app.services.job_errors import ConflictError, NotFoundError

EntityType = Literal["node_code", "agent", "executor"]
EntityStatus = Literal["draft", "published", "archived"]

_COLUMNS = (
    "id, entity_type, workspace_id, entity_key, version, status,"
    " definition_json, definition_hash, created_by, created_at, published_at"
)
# workspace_id is NULL for global entities; IS NOT DISTINCT FROM matches
# NULL-safe so the same filter serves scoped and global entities.
_ENTITY_FILTER = "entity_type=%s and workspace_id is not distinct from %s and entity_key=%s"


@dataclass(frozen=True)
class VersionedEntity:
    """One immutable version row of a versioned entity."""

    id: str
    entity_type: EntityType
    workspace_id: str | None
    entity_key: str
    version: int
    status: EntityStatus
    definition: dict[str, Any]
    definition_hash: str
    created_by: str
    created_at: datetime
    published_at: datetime | None


def _to_entity(row: dict[str, Any]) -> VersionedEntity:
    return VersionedEntity(
        id=str(row["id"]),
        entity_type=row["entity_type"],
        workspace_id=row["workspace_id"],
        entity_key=str(row["entity_key"]),
        version=int(row["version"]),
        status=row["status"],
        definition=json.loads(row["definition_json"]),
        definition_hash=str(row["definition_hash"]),
        created_by=str(row["created_by"]),
        created_at=row["created_at"],
        published_at=row["published_at"],
    )


def _serialize_definition(definition: dict[str, Any]) -> str:
    return json.dumps(definition, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


# Partial unique index guarding one published Agent per capability per workspace.
_CAPABILITY_INDEX = "versioned_entities_published_capability"


def _integrity_conflict(exc: IntegrityError, entity_type: EntityType) -> ConflictError:
    """Map index violations to ConflictError with a capability-aware message."""
    diag = getattr(exc, "diag", None)
    constraint = getattr(diag, "constraint_name", None) if diag is not None else None
    if constraint == _CAPABILITY_INDEX:
        return ConflictError(
            f"capability is already published by another {entity_type};"
            " exactly one published entity per capability in the workspace"
        )
    return ConflictError("entity version allocated concurrently; retry")


class VersionedEntityStore:
    """Draft → published → archived lifecycle engine for one entity type.

    ``database_dsn`` accepts the JobQueries facade or a bare DSN
    (BOUNDARY-DATA-001, #187); it only feeds the connection helpers.
    """

    def __init__(self, database_dsn: ConnectSource, entity_type: EntityType) -> None:
        self._dsn = database_dsn
        self._entity_type = entity_type

    @property
    def dsn(self) -> ConnectSource:
        """Connect-source identity for cache keys (facade or DSN)."""
        return self._dsn

    def get_published(self, entity_key: str, workspace_id: str | None) -> VersionedEntity | None:
        with read_connection(self._dsn) as conn:
            row = conn.execute(
                f"select {_COLUMNS} from versioned_entities"
                f" where {_ENTITY_FILTER} and status='published'",
                (self._entity_type, workspace_id, entity_key),
            ).fetchone()
        return _to_entity(dict(row)) if row else None

    def get_version(
        self, entity_key: str, version: int, workspace_id: str | None
    ) -> VersionedEntity | None:
        """Return any version (including archived) — frozen jobs read these."""
        with read_connection(self._dsn) as conn:
            row = conn.execute(
                f"select {_COLUMNS} from versioned_entities where {_ENTITY_FILTER} and version=%s",
                (self._entity_type, workspace_id, entity_key, version),
            ).fetchone()
        return _to_entity(dict(row)) if row else None

    def list_versions(self, entity_key: str, workspace_id: str | None) -> list[VersionedEntity]:
        with read_connection(self._dsn) as conn:
            rows = conn.execute(
                f"select {_COLUMNS} from versioned_entities"
                f" where {_ENTITY_FILTER} order by version desc",
                (self._entity_type, workspace_id, entity_key),
            ).fetchall()
        return [_to_entity(dict(row)) for row in rows]

    def list_latest(self, workspace_id: str | None) -> list[VersionedEntity]:
        """Latest version per entity key within one scope (draft beats published)."""
        with read_connection(self._dsn) as conn:
            rows = conn.execute(
                f"select distinct on (entity_key) {_COLUMNS} from versioned_entities"
                " where entity_type=%s and workspace_id is not distinct from %s"
                " order by entity_key, version desc",
                (self._entity_type, workspace_id),
            ).fetchall()
        return [_to_entity(dict(row)) for row in rows]

    def list_published_keys(
        self, workspace_id: str | None, entity_keys: list[str]
    ) -> list[VersionedEntity]:
        """Published rows for a batch of keys (intake freeze reads these)."""
        if not entity_keys:
            return []
        with read_connection(self._dsn) as conn:
            rows = conn.execute(
                f"select {_COLUMNS} from versioned_entities"
                " where entity_type=%s and workspace_id is not distinct from %s"
                " and entity_key = any(%s) and status='published'",
                (self._entity_type, workspace_id, entity_keys),
            ).fetchall()
        return [_to_entity(dict(row)) for row in rows]

    def list_published(self, workspace_id: str | None) -> list[VersionedEntity]:
        """Every published row within one scope."""
        with read_connection(self._dsn) as conn:
            rows = conn.execute(
                f"select {_COLUMNS} from versioned_entities"
                " where entity_type=%s and workspace_id is not distinct from %s"
                " and status='published'",
                (self._entity_type, workspace_id),
            ).fetchall()
        return [_to_entity(dict(row)) for row in rows]

    def save_draft(
        self,
        entity_key: str,
        definition: dict[str, Any],
        definition_hash: str,
        workspace_id: str | None,
        created_by: str,
    ) -> VersionedEntity:
        """Create a draft version, overwriting the existing draft when present.

        The next version is ``max(version) + 1`` inside the write transaction;
        the ``(entity_type, workspace, key, version)`` unique constraint
        serializes concurrent writers.
        """
        with write_transaction(self._dsn) as conn:
            draft = _latest_with_status(conn, self._entity_type, workspace_id, entity_key, "draft")
            if draft is not None:
                # Guard the status transition: a concurrent publish between the
                # select above and this update must not let the write land on
                # an already-published (immutable) row.
                cursor = conn.execute(
                    "update versioned_entities set definition_json=%s, definition_hash=%s,"
                    " created_by=%s, created_at=current_timestamp"
                    " where id=%s and status='draft'",
                    (_serialize_definition(definition), definition_hash, created_by, draft["id"]),
                )
                if cursor.rowcount == 0:
                    raise ConflictError("entity draft changed concurrently; reload and retry")
                return _get_entity_by_id(conn, draft["id"])
            version = _next_version(conn, self._entity_type, workspace_id, entity_key)
            row_id = uuid.uuid4().hex
            try:
                conn.execute(
                    f"insert into versioned_entities({_COLUMNS})"
                    " values (%s, %s, %s, %s, %s, 'draft', %s, %s, %s,"
                    " current_timestamp, null)",
                    (
                        row_id,
                        self._entity_type,
                        workspace_id,
                        entity_key,
                        version,
                        _serialize_definition(definition),
                        definition_hash,
                        created_by,
                    ),
                )
            except IntegrityError as exc:
                raise ConflictError("entity version allocated concurrently; retry") from exc
            return _get_entity_by_id(conn, row_id)

    def publish(self, entity_key: str, workspace_id: str | None) -> VersionedEntity:
        """Publish the current draft; the previously published version archives."""
        with write_transaction(self._dsn) as conn:
            draft = _latest_with_status(conn, self._entity_type, workspace_id, entity_key, "draft")
            if draft is None:
                raise NotFoundError(f"no draft for {self._entity_type} {entity_key}")
            conn.execute(
                "update versioned_entities set status='archived'"
                f" where {_ENTITY_FILTER} and status='published'",
                (self._entity_type, workspace_id, entity_key),
            )
            # Guard: a concurrent archive_all between select and update must
            # not resurrect an archived row into published.
            try:
                cursor = conn.execute(
                    "update versioned_entities set status='published',"
                    " published_at=current_timestamp where id=%s and status='draft'",
                    (draft["id"],),
                )
            except IntegrityError as exc:
                raise _integrity_conflict(exc, self._entity_type) from exc
            if cursor.rowcount == 0:
                raise ConflictError("entity draft changed concurrently; reload and retry")
            return _get_entity_by_id(conn, draft["id"])

    def rollback(
        self,
        entity_key: str,
        version: int,
        workspace_id: str | None,
        created_by: str,
        definition_patch: dict[str, Any] | None = None,
    ) -> VersionedEntity:
        """Re-publish an old version as a new version (versions stay immutable).

        ``definition_patch`` merges into the copied definition (e.g. a rollback
        change note); the definition hash always stays the source version's.
        """
        with write_transaction(self._dsn) as conn:
            source = conn.execute(
                f"select {_COLUMNS} from versioned_entities where {_ENTITY_FILTER} and version=%s",
                (self._entity_type, workspace_id, entity_key, version),
            ).fetchone()
            if source is None:
                raise NotFoundError(f"no version {version} for {self._entity_type} {entity_key}")
            conn.execute(
                "update versioned_entities set status='archived'"
                f" where {_ENTITY_FILTER} and status='published'",
                (self._entity_type, workspace_id, entity_key),
            )
            definition_json = str(source["definition_json"])
            if definition_patch:
                definition_json = _serialize_definition(
                    {**json.loads(definition_json), **definition_patch}
                )
            row_id = uuid.uuid4().hex
            try:
                conn.execute(
                    f"insert into versioned_entities({_COLUMNS})"
                    " values (%s, %s, %s, %s, %s, 'published', %s, %s, %s,"
                    " current_timestamp, current_timestamp)",
                    (
                        row_id,
                        self._entity_type,
                        workspace_id,
                        entity_key,
                        _next_version(conn, self._entity_type, workspace_id, entity_key),
                        definition_json,
                        source["definition_hash"],
                        created_by,
                    ),
                )
            except IntegrityError as exc:
                raise _integrity_conflict(exc, self._entity_type) from exc
            return _get_entity_by_id(conn, row_id)

    def archive_all(self, entity_key: str, workspace_id: str | None) -> int:
        """Archive every version of the entity; returns the archived count."""
        with write_transaction(self._dsn) as conn:
            cursor = conn.execute(
                "update versioned_entities set status='archived'"
                f" where {_ENTITY_FILTER} and status != 'archived'",
                (self._entity_type, workspace_id, entity_key),
            )
            return cursor.rowcount

    def copy(
        self,
        source_key: str,
        new_key: str,
        workspace_id: str | None,
        created_by: str,
    ) -> VersionedEntity:
        """Copy the latest source definition into a new entity as draft v1."""
        with write_transaction(self._dsn) as conn:
            source = conn.execute(
                f"select {_COLUMNS} from versioned_entities where {_ENTITY_FILTER}"
                " order by version desc limit 1",
                (self._entity_type, workspace_id, source_key),
            ).fetchone()
            if source is None:
                raise NotFoundError(f"no {self._entity_type} {source_key} to copy")
            row_id = uuid.uuid4().hex
            try:
                conn.execute(
                    f"insert into versioned_entities({_COLUMNS})"
                    " values (%s, %s, %s, %s, 1, 'draft', %s, %s, %s,"
                    " current_timestamp, null)",
                    (
                        row_id,
                        self._entity_type,
                        workspace_id,
                        new_key,
                        source["definition_json"],
                        source["definition_hash"],
                        created_by,
                    ),
                )
            except IntegrityError as exc:
                raise ConflictError(f"{self._entity_type} {new_key} already exists") from exc
            return _get_entity_by_id(conn, row_id)


def _latest_with_status(
    conn: Any, entity_type: str, workspace_id: str | None, entity_key: str, status: str
) -> dict[str, Any] | None:
    row = conn.execute(
        f"select {_COLUMNS} from versioned_entities"
        f" where {_ENTITY_FILTER} and status=%s order by version desc limit 1",
        (entity_type, workspace_id, entity_key, status),
    ).fetchone()
    return dict(row) if row else None


def _next_version(conn: Any, entity_type: str, workspace_id: str | None, entity_key: str) -> int:
    row = conn.execute(
        "select coalesce(max(version), 0) + 1 as next_version from versioned_entities"
        " where entity_type=%s and workspace_id is not distinct from %s and entity_key=%s",
        (entity_type, workspace_id, entity_key),
    ).fetchone()
    return int(row["next_version"]) if row is not None else 1


def _get_entity_by_id(conn: Any, row_id: str) -> VersionedEntity:
    row = conn.execute(
        f"select {_COLUMNS} from versioned_entities where id=%s", (row_id,)
    ).fetchone()
    if row is None:  # pragma: no cover - defensive; the row was just written
        raise NotFoundError(f"versioned entity row vanished: {row_id}")
    return _to_entity(dict(row))
