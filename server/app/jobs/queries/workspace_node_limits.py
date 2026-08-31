from __future__ import annotations

from server.app.db.connection import DatabaseConnection


def get_local_node_limit(
    conn: DatabaseConnection,
    workspace_id: str,
    workflow_key: str,
    node_key: str,
) -> int | None:
    """#211 Phase 3 (read-layer binding): the predicate keys on
    (workspace_id, node_key) — workflow_key equals the workspace id on every
    row (v62 binding, aligned by v68), so the column filter was redundant.
    The signature parameter stays for callers until Phase 4 drops the column.
    """
    row = conn.execute(
        """
        select concurrency_limit from workspace_node_limits
        where workspace_id=%s and node_key=%s
        """,
        (workspace_id, node_key),
    ).fetchone()
    return int(row["concurrency_limit"]) if row else None
