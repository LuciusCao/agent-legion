"""DB-backed custom workflow node codes (EXEC-CODE-002).

Custom node code is user data, not a repo asset: versions live in the
``workflow_node_codes`` table, are immutable, and take effect only through the
publish flow (draft → published → archived). At most one published version
exists per ``(workspace, workflow, node)`` (partial unique index); archiving
every version falls the node back to the builtin repo-tracked implementation
(EXEC-CODE-001).

The feature is gated by ``workflows.custom_nodes_enabled`` (default on in this
phase, design §7); every public entry point checks the gate before validating
and raises ``CustomNodesDisabledError`` when it is off.
"""

from __future__ import annotations

import ast
import hashlib
import uuid
from typing import Any

from server.app.db.connection import DatabaseDsn
from server.app.db.transaction import read_connection, write_transaction
from server.app.services.job_errors import (
    CustomNodesDisabledError,
    InvalidOperationError,
    NotFoundError,
)

# Custom nodes stay single-file and cohesive; oversized code is rejected.
MAX_CODE_BYTES = 64 * 1024

_COLUMNS = (
    "id, workspace_id, workflow_key, node_key, version, status, code, code_hash,"
    " created_by, change_note, created_at, published_at"
)
_NODE_FILTER = "workspace_id=%s and workflow_key=%s and node_key=%s"


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
    """Versioned custom node code storage and publish flow."""

    def __init__(self, database_dsn: DatabaseDsn, custom_nodes_enabled: bool = True) -> None:
        self._dsn = database_dsn
        self._enabled = custom_nodes_enabled

    def _require_enabled(self) -> None:
        if not self._enabled:
            raise CustomNodesDisabledError("custom workflow nodes are disabled")

    def get_effective_code(
        self, workspace_id: str, workflow_key: str, node_key: str
    ) -> dict[str, Any] | None:
        """Return the published version row, or None when the node is builtin."""
        self._require_enabled()
        with read_connection(self._dsn) as conn:
            row = conn.execute(
                f"select {_COLUMNS} from workflow_node_codes"
                f" where {_NODE_FILTER} and status='published'",
                (workspace_id, workflow_key, node_key),
            ).fetchone()
        return dict(row) if row else None

    def get_code_by_version(
        self, workspace_id: str, workflow_key: str, node_key: str, version: int
    ) -> dict[str, Any] | None:
        """Return any version row (including archived) — frozen jobs read these."""
        self._require_enabled()
        with read_connection(self._dsn) as conn:
            row = conn.execute(
                f"select {_COLUMNS} from workflow_node_codes where {_NODE_FILTER} and version=%s",
                (workspace_id, workflow_key, node_key, version),
            ).fetchone()
        return dict(row) if row else None

    def list_versions(
        self, workspace_id: str, workflow_key: str, node_key: str
    ) -> list[dict[str, Any]]:
        self._require_enabled()
        with read_connection(self._dsn) as conn:
            rows = conn.execute(
                f"select {_COLUMNS} from workflow_node_codes"
                f" where {_NODE_FILTER} order by version desc",
                (workspace_id, workflow_key, node_key),
            ).fetchall()
        return [dict(row) for row in rows]

    def save_draft(
        self,
        workspace_id: str,
        workflow_key: str,
        node_key: str,
        code: str,
        created_by: str,
        change_note: str | None = None,
    ) -> dict[str, Any]:
        """Create a draft version, overwriting the existing draft when present.

        The next version is ``max(version) + 1`` inside the write transaction;
        the ``(workspace, workflow, node, version)`` unique constraint
        serializes concurrent writers.
        """
        self._require_enabled()
        validate_node_code(code)
        with write_transaction(self._dsn) as conn:
            draft = _latest_with_status(conn, workspace_id, workflow_key, node_key, "draft")
            if draft is not None:
                conn.execute(
                    "update workflow_node_codes set code=%s, code_hash=%s, created_by=%s,"
                    " change_note=%s, created_at=current_timestamp where id=%s",
                    (code, code_hash(code), created_by, change_note, draft["id"]),
                )
                return _get_by_id(conn, draft["id"])
            version = _next_version(conn, workspace_id, workflow_key, node_key)
            row_id = uuid.uuid4().hex
            conn.execute(
                f"insert into workflow_node_codes({_COLUMNS})"
                " values (%s, %s, %s, %s, %s, 'draft', %s, %s, %s, %s,"
                " current_timestamp, null)",
                (
                    row_id,
                    workspace_id,
                    workflow_key,
                    node_key,
                    version,
                    code,
                    code_hash(code),
                    created_by,
                    change_note,
                ),
            )
            return _get_by_id(conn, row_id)

    def publish(self, workspace_id: str, workflow_key: str, node_key: str) -> dict[str, Any]:
        """Publish the current draft; the previously published version archives."""
        self._require_enabled()
        with write_transaction(self._dsn) as conn:
            draft = _latest_with_status(conn, workspace_id, workflow_key, node_key, "draft")
            if draft is None:
                raise NotFoundError(f"no draft node code for {workflow_key}/{node_key}")
            conn.execute(
                "update workflow_node_codes set status='archived'"
                f" where {_NODE_FILTER} and status='published'",
                (workspace_id, workflow_key, node_key),
            )
            conn.execute(
                "update workflow_node_codes set status='published',"
                " published_at=current_timestamp where id=%s",
                (draft["id"],),
            )
            return _get_by_id(conn, draft["id"])

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
        with write_transaction(self._dsn) as conn:
            source = conn.execute(
                f"select {_COLUMNS} from workflow_node_codes where {_NODE_FILTER} and version=%s",
                (workspace_id, workflow_key, node_key, version),
            ).fetchone()
            if source is None:
                raise NotFoundError(f"no node code version {version} for {workflow_key}/{node_key}")
            conn.execute(
                "update workflow_node_codes set status='archived'"
                f" where {_NODE_FILTER} and status='published'",
                (workspace_id, workflow_key, node_key),
            )
            row_id = uuid.uuid4().hex
            conn.execute(
                f"insert into workflow_node_codes({_COLUMNS})"
                " values (%s, %s, %s, %s, %s, 'published', %s, %s, %s, %s,"
                " current_timestamp, current_timestamp)",
                (
                    row_id,
                    workspace_id,
                    workflow_key,
                    node_key,
                    _next_version(conn, workspace_id, workflow_key, node_key),
                    source["code"],
                    source["code_hash"],
                    created_by,
                    change_note if change_note is not None else f"rollback to v{version}",
                ),
            )
            return _get_by_id(conn, row_id)

    def archive_all(self, workspace_id: str, workflow_key: str, node_key: str) -> int:
        """Archive every version; the node falls back to the builtin implementation."""
        self._require_enabled()
        with write_transaction(self._dsn) as conn:
            cursor = conn.execute(
                "update workflow_node_codes set status='archived'"
                f" where {_NODE_FILTER} and status != 'archived'",
                (workspace_id, workflow_key, node_key),
            )
            return cursor.rowcount


def _latest_with_status(
    conn: Any, workspace_id: str, workflow_key: str, node_key: str, status: str
) -> dict[str, Any] | None:
    row = conn.execute(
        f"select {_COLUMNS} from workflow_node_codes"
        f" where {_NODE_FILTER} and status=%s order by version desc limit 1",
        (workspace_id, workflow_key, node_key, status),
    ).fetchone()
    return dict(row) if row else None


def _next_version(conn: Any, workspace_id: str, workflow_key: str, node_key: str) -> int:
    row = conn.execute(
        "select coalesce(max(version), 0) + 1 as next_version from workflow_node_codes"
        f" where {_NODE_FILTER}",
        (workspace_id, workflow_key, node_key),
    ).fetchone()
    return int(row["next_version"]) if row is not None else 1


def _get_by_id(conn: Any, row_id: str) -> dict[str, Any]:
    row = conn.execute(
        f"select {_COLUMNS} from workflow_node_codes where id=%s", (row_id,)
    ).fetchone()
    if row is None:  # pragma: no cover - defensive; the row was just written
        raise NotFoundError(f"node code row vanished: {row_id}")
    return dict(row)


def freeze_node_code_versions(
    database_dsn: DatabaseDsn,
    custom_nodes_enabled: bool,
    workspace_id: str,
    workflow_key: str,
    node_keys: list[str],
) -> dict[str, dict[str, Any]]:
    """Intake freeze: published ``{node_key: {version, code_hash}}`` pins.

    Only nodes with a published custom version appear; the gate short-circuits
    to an empty mapping so intake never touches the table when the feature is
    off.
    """
    if not custom_nodes_enabled:
        return {}
    service = NodeCodeService(database_dsn)
    pins: dict[str, dict[str, Any]] = {}
    for node_key in node_keys:
        row = service.get_effective_code(workspace_id, workflow_key, node_key)
        if row is not None:
            pins[node_key] = {"version": row["version"], "code_hash": row["code_hash"]}
    return pins


def resolve_dispatch_node_code(
    database_dsn: DatabaseDsn,
    custom_nodes_enabled: bool,
    workspace_id: str,
    workflow_key: str,
    node_key: str,
    frozen: dict[str, Any] | None,
) -> str | None:
    """Dispatch-time code text: frozen job version → published → None (builtin).

    One DB read per dispatch, same cadence as the vault secret resolution it
    runs next to; the 30s route cache in ``routing.py`` only covers executor
    bindings and is deliberately not consulted here. The gate short-circuits
    to builtin instead of raising so a disabled feature never breaks dispatch.
    """
    if not custom_nodes_enabled:
        return None
    service = NodeCodeService(database_dsn)
    if frozen is not None:
        row = service.get_code_by_version(
            workspace_id, workflow_key, node_key, int(frozen["version"])
        )
        if row is not None:
            return str(row["code"])
    row = service.get_effective_code(workspace_id, workflow_key, node_key)
    return str(row["code"]) if row is not None else None
