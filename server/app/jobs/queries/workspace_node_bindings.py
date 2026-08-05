from __future__ import annotations

from typing import Any

from server.app.db.connection import DatabaseConnection


def get_binding(
    conn: DatabaseConnection,
    workspace_id: str,
    workflow_key: str,
    node_key: str,
) -> dict[str, Any] | None:
    row = conn.execute(
        """
        select executor_id from workspace_node_bindings
        where workspace_id=%s and workflow_key=%s and node_key=%s
        """,
        (workspace_id, workflow_key, node_key),
    ).fetchone()
    return dict(row) if row else None


def get_local_node_limit(
    conn: DatabaseConnection,
    workspace_id: str,
    workflow_key: str,
    node_key: str,
) -> int | None:
    row = conn.execute(
        """
        select concurrency_limit from workspace_node_limits
        where workspace_id=%s and workflow_key=%s and node_key=%s
        """,
        (workspace_id, workflow_key, node_key),
    ).fetchone()
    return int(row["concurrency_limit"]) if row else None


def has_local_node_limit(
    conn: DatabaseConnection,
    workspace_id: str,
    workflow_key: str,
    node_key: str,
) -> bool:
    row = conn.execute(
        """
        select 1 from workspace_node_limits
        where workspace_id=%s and workflow_key=%s and node_key=%s
        """,
        (workspace_id, workflow_key, node_key),
    ).fetchone()
    return row is not None
