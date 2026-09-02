"""Experimental shard fan-out state for sharded workflow nodes.

Shard/reduce is an internal experimental capability and is not supported for
production workflow revisions yet.  In particular, Workflow Studio does not
round-trip the declarations and local concurrent shards do not have isolated
output directories.  Keep production workflows on ordinary DAG fan-out until
those gaps are closed.

``node_shards`` rows are the per-shard execution records of a node that
declares ``shard:`` in the workflow definition. The node's ``job_nodes`` row
stays the single aggregate authority; shard rows never leak into the node
state machine directly. Every shard execution is claimed through the
executor lease system (EXEC-SHARD-001) — this module only owns the
shard-table reads and writes behind those claim/finish paths.

All functions take an open connection from the caller's ``write_transaction``
(pure queries also accept a read connection); none of them manages
transactions or connections.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from datetime import datetime

from server.app.db.connection import DatabaseConnection
from server.app.workflows.sharding_finish import (
    ShardStatus,
    aggregate_shard_state,
    on_shard_finished,
)

__all__ = [
    "ShardLimitExceeded",
    "ShardStatus",
    "aggregate_shard_state",
    "delete_shards",
    "failed_shard_error",
    "materialize_shards",
    "on_shard_finished",
    "read_shard_outputs",
    "shard_index_for_execution",
    "try_start_shard",
]

_RUNNABLE_NODE_STATUSES = ("pending", "ready", "stale", "running")


class ShardLimitExceeded(Exception):
    """Raised when a shard fan-out exceeds the node's ``max_shards`` limit."""


def materialize_shards(
    conn: DatabaseConnection,
    job_id: str,
    node_key: str,
    inputs: list[dict],
    *,
    max_shards: int,
) -> int:
    """Insert one pending shard row per input in a single executemany batch.

    Idempotent: re-materializing the same (job_id, node_key) is a no-op for
    existing rows (``on conflict do nothing``). Returns the total number of shard
    rows for the node after the call. Raises :class:`ShardLimitExceeded`
    before writing anything when ``inputs`` exceeds ``max_shards``.
    """
    if len(inputs) > max_shards:
        raise ShardLimitExceeded(
            f"shard fan-out of {len(inputs)} exceeds max_shards {max_shards} for {node_key}"
        )
    conn.executemany(
        """
        insert into node_shards(job_id, node_key, shard_index, input_json)
        values (%s, %s, %s, %s) on conflict(job_id, node_key, shard_index) do nothing
        """,
        [(job_id, node_key, index, json.dumps(item)) for index, item in enumerate(inputs)],
    )
    row = conn.execute(
        "select count(*) as cnt from node_shards where job_id=%s and node_key=%s",
        (job_id, node_key),
    ).fetchone()
    return int(row["cnt"]) if row is not None else 0


def try_start_shard(
    conn: DatabaseConnection,
    job_id: str,
    node_key: str,
    shard_index: int,
    execution_id: str,
    started_at: datetime | str,
) -> bool:
    """Flip one pending shard to running under its own execution_id.

    Returns False when the shard is no longer claimable (already claimed, or
    the owning node left a runnable state). Also flips ``job_nodes`` to
    running on the first shard claim; later shard claims leave it running.
    """
    node = conn.execute(
        "select status from job_nodes where job_id=%s and node_key=%s",
        (job_id, node_key),
    ).fetchone()
    if node is None or node["status"] not in _RUNNABLE_NODE_STATUSES:
        return False
    cursor = conn.execute(
        """
        update node_shards
        set status='running', execution_id=%s, started_at=%s, error_message='', finished_at=null
        where job_id=%s and node_key=%s and shard_index=%s and status='pending'
        """,
        (execution_id, started_at, job_id, node_key, shard_index),
    )
    if cursor.rowcount == 0:
        return False
    conn.execute(
        """
        update job_nodes
        set status='running', stale_reason='', error_message='', started_at=%s, finished_at=null
        where job_id=%s and node_key=%s and status in ('pending', 'ready', 'stale')
        """,
        (started_at, job_id, node_key),
    )
    return True


def shard_index_for_execution(
    conn: DatabaseConnection, job_id: str, node_key: str, execution_id: str
) -> int | None:
    """Return the shard index claimed under ``execution_id``, or None."""
    row = conn.execute(
        "select shard_index from node_shards where job_id=%s and node_key=%s and execution_id=%s",
        (job_id, node_key, execution_id),
    ).fetchone()
    return int(row["shard_index"]) if row is not None else None


def failed_shard_error(conn: DatabaseConnection, job_id: str, node_key: str) -> str:
    """Return the first failed shard's error message (deterministic aggregate error)."""
    row = conn.execute(
        """
        select error_message from node_shards
        where job_id=%s and node_key=%s and status='failed'
        order by shard_index limit 1
        """,
        (job_id, node_key),
    ).fetchone()
    return str(row["error_message"]) if row is not None else ""


def read_shard_outputs(conn: DatabaseConnection, job_id: str, node_key: str) -> list[str]:
    """Return shard ``output_json`` payloads ordered by shard_index."""
    rows = conn.execute(
        "select output_json from node_shards where job_id=%s and node_key=%s order by shard_index",
        (job_id, node_key),
    ).fetchall()
    return [str(row["output_json"]) for row in rows]


def delete_shards(
    conn: DatabaseConnection,
    job_id: str,
    node_keys: Iterable[str],
) -> int:
    """Delete shard rows for reset nodes so the next tick rematerializes them."""
    keys = sorted(set(node_keys))
    if not keys:
        return 0
    placeholders = ",".join("%s" for _ in keys)
    cursor = conn.execute(
        f"delete from node_shards where job_id=%s and node_key in ({placeholders})",
        (job_id, *keys),
    )
    return cursor.rowcount
