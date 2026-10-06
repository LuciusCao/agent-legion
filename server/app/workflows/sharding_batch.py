"""Batch sharding queries for the workflow worker.

``has_pending_shards_many`` lets the poll pass ask about all running shard
aggregates in a single query instead of one round trip per node.

``running_shard_nodes`` / ``shard_effective_statuses`` are the single
definition of the shard-effective status flip (#759 review P2): a running
shard node that still has pending shards counts as ``pending``. The worker's
hydration pass and the job-detail hydration-defer projection (#1021) both
derive it from here so the two never disagree.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping

from server.app.db.connection import DatabaseConnection
from server.app.workflows.definition import WorkflowDefinition


def running_shard_nodes(definition: WorkflowDefinition, statuses: Mapping[str, str]) -> list[str]:
    """Shard nodes whose aggregate row is ``running`` — the pending-shard probe set."""
    return [
        node.key
        for node in definition.nodes.values()
        if node.shard is not None and statuses.get(node.key) == "running"
    ]


def shard_effective_statuses(
    statuses: dict[str, str], pending_shard_nodes: Collection[str]
) -> dict[str, str]:
    """Flip probed shard nodes that still have pending shards back to ``pending``.

    ``pending_shard_nodes`` must come from ``has_pending_shards_many`` over
    ``running_shard_nodes``. The flip rides a copy; with nothing to flip the
    input mapping is returned as-is.
    """
    if not pending_shard_nodes:
        return statuses
    return {**statuses, **{node_key: "pending" for node_key in pending_shard_nodes}}


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
