"""Workflow runtime config version persistence (schema v102, M47).

The append-only history behind the runtime channel: every runtime-only
in-place save (``PATCH workflow-runtime``, a rollback, or a runtime-only
draft publish) inserts one immutable row inside the same transaction — and
under the same implementation-publication lock — that rewrites the active
revision's definition_json. Dispatch never reads this table: the active
revision stays the single resolution source; the rows here are the audit
trail and the rollback source.

Writer helpers take the caller's transaction connection and never commit on
their own (same contract as ``workflow_revision_projection``); reads sit on
the mixin so services stay behind the JobQueries facade (BOUNDARY-DATA-001).
"""

from __future__ import annotations

import uuid
from typing import Any

from server.app.db.connection import DatabaseConnection
from server.app.jobs.queries.connection import ConnectionQueriesMixin

_COLUMNS = (
    "id, workspace_id, revision_id, version, config_json, changed_keys_json, created_by, created_at"
)


def update_active_revision_definition_tx(
    conn: DatabaseConnection,
    *,
    revision_id: str,
    definition_json: str,
    definition_hash: str,
) -> dict[str, Any]:
    """Rewrite the active revision row in place on the caller's transaction.

    Runtime-only edits keep the revision id/version (the workflow structure is
    unchanged); the caller holds the implementation-publication lock and has
    revalidated the row's identity, so the write cannot land on an archived
    row. Returns the updated row.
    """
    row = conn.execute(
        "update workflow_revisions set definition_json=%s, definition_hash=%s"
        " where id=%s returning *",
        (definition_json, definition_hash, revision_id),
    ).fetchone()
    if row is None:  # pragma: no cover - the caller re-read the row under the lock
        raise ValueError("workflow revision not found")
    return dict(row)


def append_runtime_config_version_tx(
    conn: DatabaseConnection,
    *,
    workspace_id: str,
    revision_id: str,
    config_json: str,
    changed_keys_json: str,
    created_by: str,
) -> dict[str, Any]:
    """Insert one immutable runtime config version on the caller's transaction.

    The next version is ``max(version) + 1`` read inside the same transaction;
    the caller holds the implementation-publication lock, so the allocation
    cannot race a concurrent runtime save — the ``(workspace_id, version)``
    unique index is the backstop.
    """
    row = conn.execute(
        "select coalesce(max(version), 0) + 1 as next_version"
        " from workflow_runtime_config_versions where workspace_id=%s",
        (workspace_id,),
    ).fetchone()
    next_version = int(row["next_version"]) if row is not None else 1
    row = conn.execute(
        f"insert into workflow_runtime_config_versions({_COLUMNS})"
        " values (%s, %s, %s, %s, %s, %s, %s, current_timestamp)"
        f" returning {_COLUMNS}",
        (
            uuid.uuid4().hex,
            workspace_id,
            revision_id,
            next_version,
            config_json,
            changed_keys_json,
            created_by,
        ),
    ).fetchone()
    if row is None:  # pragma: no cover - defensive; the row was just written
        raise ValueError("runtime config version insert did not return a row")
    return dict(row)


class WorkflowRuntimeConfigQueriesMixin(ConnectionQueriesMixin):
    """Read surface for ``workflow_runtime_config_versions`` rows."""

    def list_runtime_config_versions(self, workspace_id: str) -> list[dict[str, Any]]:
        """Every runtime config version of the workspace, newest first."""
        with self._connect_read() as conn:
            rows = conn.execute(
                f"select {_COLUMNS} from workflow_runtime_config_versions"
                " where workspace_id=%s order by version desc",
                (workspace_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_runtime_config_version(self, workspace_id: str, version: int) -> dict[str, Any] | None:
        with self._connect_read() as conn:
            row = conn.execute(
                f"select {_COLUMNS} from workflow_runtime_config_versions"
                " where workspace_id=%s and version=%s",
                (workspace_id, version),
            ).fetchone()
        return dict(row) if row else None
