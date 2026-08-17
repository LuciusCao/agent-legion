from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from server.app.db.connection import DatabaseConnection


def get_workspace_node_limits(conn: DatabaseConnection, workspace_id: str) -> list[dict[str, Any]]:
    """Per-node concurrency limits of one workspace (P-0.5: the only node knob)."""
    rows = conn.execute(
        "select workflow_key, node_key, concurrency_limit "
        "from workspace_node_limits where workspace_id=%s order by workflow_key, node_key",
        (workspace_id,),
    ).fetchall()
    return [dict(row) for row in rows]


def replace_workspace_node_limits(
    conn: DatabaseConnection,
    workspace_id: str,
    node_limits: Sequence[Mapping[str, Any]],
) -> None:
    conn.execute("delete from workspace_node_limits where workspace_id=%s", (workspace_id,))
    conn.executemany(
        "insert into workspace_node_limits "
        "(workspace_id, workflow_key, node_key, concurrency_limit) values (%s, %s, %s, %s)",
        [
            (workspace_id, row["workflow_key"], row["node_key"], row["concurrency_limit"])
            for row in node_limits
        ],
    )
