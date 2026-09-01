"""Shard aggregate state + the lost-update guard for shard finishes.

Extracted from ``workflows/sharding.py`` when the row-lock fix pushed that
file past its committed line budget. This module owns the node-aggregate
semantics (``aggregate_shard_state``) and the one write path where a shard's
terminal status meets that aggregate (``on_shard_finished``) — the only
place in the system that advances a sharded node off its in-flight state,
and the place where a READ COMMITTED race could otherwise wedge a job
permanently (see ``on_shard_finished``). ``sharding.py`` re-exports both
symbols, so callers keep importing from there.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from server.app.db.connection import DatabaseConnection

ShardStatus = Literal["pending", "running", "completed", "failed"]


def _now() -> datetime:
    return datetime.now(UTC)


def aggregate_shard_state(conn: DatabaseConnection, job_id: str, node_key: str) -> ShardStatus:
    """Collapse shard statuses into the node's aggregate state.

    Precedence: all completed -> ``completed``; any failed -> ``failed`` (a
    shard row only reads ``failed`` once the caller recorded the terminal
    failure — retry/reset decisions stay with the caller); any running ->
    ``running``; otherwise ``pending``.
    """
    rows = conn.execute(
        "select status from node_shards where job_id=%s and node_key=%s",
        (job_id, node_key),
    ).fetchall()
    if not rows:
        return "pending"
    statuses = {str(row["status"]) for row in rows}
    if statuses == {"completed"}:
        return "completed"
    if "failed" in statuses:
        return "failed"
    if "running" in statuses:
        return "running"
    return "pending"


def on_shard_finished(
    conn: DatabaseConnection,
    job_id: str,
    node_key: str,
    shard_index: int,
    status: ShardStatus,
    output_json: str = "",
    error_message: str = "",
) -> ShardStatus:
    """Record a shard's terminal status and return the aggregate state.

    The caller (lease finish path) decides from the aggregate whether the
    owning ``job_nodes`` row advances; intermediate aggregates must not touch
    the node state machine.

    The aggregate read must not lose updates: under READ COMMITTED two
    concurrent finish transactions for the last two shards would each see
    the peer's update as still in flight, both compute a non-terminal
    aggregate, and the node would wedge in ``running`` forever with every
    shard committed terminal — a lost update with no reconciliation path
    anywhere else. The ordered ``for update`` scan serializes concurrent
    finishers of the same node: the scan runs BEFORE the own-shard update
    (a tx that updates its own shard first and then scans can deadlock
    against a peer doing the opposite), and once the scan completes the
    transaction holds every shard row of the node, so the plain aggregate
    SELECT afterwards — with READ COMMITTED's per-statement snapshot —
    observes the peer's committed status plus this update. Callers already
    run inside ``retry_on_database_conflict``, so a lock conflict with a
    differently-ordered locker is retried, not surfaced.
    """
    conn.execute(
        "select shard_index from node_shards"
        " where job_id=%s and node_key=%s order by shard_index for update",
        (job_id, node_key),
    )
    conn.execute(
        """
        update node_shards
        set status=%s, output_json=%s, error_message=%s, finished_at=%s
        where job_id=%s and node_key=%s and shard_index=%s
        """,
        (status, output_json, error_message, _now(), job_id, node_key, shard_index),
    )
    return aggregate_shard_state(conn, job_id, node_key)
