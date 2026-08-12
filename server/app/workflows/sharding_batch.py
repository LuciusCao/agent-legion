"""Batch sharding queries for the workflow worker.

``has_pending_shards_many`` lets the poll pass ask about all running shard
aggregates in a single query instead of one round trip per node.
"""

from __future__ import annotations

from server.app.db.connection import DatabaseConnection


def has_pending_shards_many(
    conn: DatabaseConnection, pairs: list[tuple[str, str]]
) -> set[tuple[str, str]]:
    """Return the (job_id, node_key) pairs that still have pending shards."""
    if not pairs:
        return set()
    placeholders = ",".join("(%s, %s)" for _ in pairs)
    values = [value for pair in pairs for value in pair]
    rows = conn.execute(
        f"""
        select distinct job_id, node_key from node_shards
        where status='pending' and (job_id, node_key) in ({placeholders})
        """,
        values,
    ).fetchall()
    return {(str(row["job_id"]), str(row["node_key"])) for row in rows}
