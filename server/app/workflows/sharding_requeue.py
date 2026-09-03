"""Shard-aware claim loss handling for the Worker-loss sweep (#389).

``sweep_expired_claims`` (agent_broker/sweepers.py) owns the generic
requeue/terminal flow; these two helpers are the shard half, split out for
the size budget. A remote shard claim binds its execution_id to a
``node_shards`` row at claim time (``try_start_shard`` via the broker claim
transaction) — when the Worker is lost, that row must be restored in the
same sweep transaction or the shard strands: the next claim's pending-only
guard would reject it forever.
"""

from __future__ import annotations

from typing import Any

from server.app.workflows.sharding import on_shard_finished, shard_index_for_execution


def reset_shard_for_requeue(conn: Any, job_id: str, node_key: str, execution_id: str) -> None:
    """Return a Worker-lost shard row to 'pending', unbound (requeue path).

    Mirrors the job_nodes reset the sweeper performs: a requeued request is
    claimed again later, and ``try_start_shard`` only flips pending rows.
    ``execution_id`` is NOT NULL with a '' default (schema), so the unbound
    state is the empty string, not NULL.
    """
    shard_index = shard_index_for_execution(conn, job_id, node_key, execution_id)
    if shard_index is None:
        return
    conn.execute(
        "update node_shards set status='pending', execution_id='',"
        " started_at=null, finished_at=null, error_message=''"
        " where job_id=%s and node_key=%s and shard_index=%s and status='running'",
        (job_id, node_key, shard_index),
    )


def fail_shard_for_dead_execution(
    conn: Any, job_id: str, node_key: str, execution_id: str, error_message: str
) -> None:
    """Fail a stranded shard row through the aggregate (terminal sweep path).

    ``on_shard_finished`` advances the shard and decides the owning node's
    aggregate state; without it the row would stay 'running' under a dead
    execution_id while the node was force-failed around it.
    """
    shard_index = shard_index_for_execution(conn, job_id, node_key, execution_id)
    if shard_index is None:
        return
    on_shard_finished(conn, job_id, node_key, shard_index, "failed", error_message=error_message)
