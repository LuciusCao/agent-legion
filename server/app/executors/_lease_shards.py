"""Shard-aware lease finish orchestration.

When a finished lease belongs to a shard execution (its ``execution_id`` was
recorded on a ``node_shards`` row at claim time), the shard row is updated
first and the aggregate state decides whether the owning ``job_nodes`` row
advances. Only terminal aggregates (``completed``/``failed``) touch the node
state machine — intermediate aggregates leave it running so in-flight shards
are not disturbed (Decision 3: shard rows are child execution records, the
node row stays the aggregate authority).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from server.app.db.connection import DatabaseConnection
from server.app.executors._lease_control import (
    _pause_job_on_target_completion,
    lock_job_mutation_and_read_generation,
    sync_job_status,
)
from server.app.executors._lease_transactions import database_timestamp
from server.app.executors.models import ExecutionResult
from server.app.workflows.sharding import (
    ShardStatus,
    failed_shard_error,
    on_shard_finished,
    shard_index_for_execution,
)

logger = logging.getLogger(__name__)


def finish_shard_execution(
    conn: DatabaseConnection,
    lease: dict[str, Any],
    result: ExecutionResult,
    now_str: str,
    *,
    generation_stale: bool = False,
) -> bool:
    """Advance shard + aggregate state for a shard lease; True when handled.

    Returns False for non-shard leases so the caller falls through to the
    regular node finish path. ``generation_stale`` (EXEC-GENERATION-001): the
    lease's epoch no longer matches jobs.execution_generation — the shard row
    (an execution record, like node_runs) still settles, but the job_nodes
    flip, status re-derivation and until_node pause are skipped so a late
    finish never overwrites a post-reset row.
    """
    shard_index = shard_index_for_execution(
        conn, lease["job_id"], lease["node_key"], lease["execution_id"]
    )
    if shard_index is None:
        return False
    status: ShardStatus = "completed" if result.status == "completed" else "failed"
    aggregate = on_shard_finished(
        conn,
        lease["job_id"],
        lease["node_key"],
        shard_index,
        status,
        output_json=result.output_json if status == "completed" else "",
        error_message=result.error_message,
    )
    if aggregate in ("completed", "failed") and not generation_stale:
        error_message = failed_shard_error(conn, lease["job_id"], lease["node_key"])
        # Status guard mirrors complete_empty_shard_node: the shard row locks
        # already serialize concurrent finishers, but a late finish racing a
        # reset/rerun must not overwrite a node that left the runnable set.
        conn.execute(
            """
            update job_nodes
            set status=%s, error_message=%s, finished_at=%s
            where job_id=%s and node_key=%s and status in ('pending', 'ready', 'stale', 'running')
            """,
            (aggregate, error_message, now_str, lease["job_id"], lease["node_key"]),
        )
        sync_job_status(conn, lease["job_id"])
        if aggregate == "completed":
            _pause_job_on_target_completion(conn, lease["job_id"], lease["node_key"], now_str)
    return True


def complete_empty_shard_node(
    conn: DatabaseConnection,
    job_id: str,
    node_key: str,
    execution_generation: int = 0,
    now_str: str | None = None,
) -> bool:
    """Complete a shard node whose fan-out materialized zero shard rows.

    Zero shards aggregate to a completed node with empty outputs — the reduce
    fan-in then reads an empty array, matching ordinary empty-list map
    semantics. The status guard makes a concurrent completion/claim a no-op
    for the loser. Returns True when this call advanced the node.

    EXEC-GENERATION-001 (#645 P3): the completion runs the same lock + epoch
    CAS as every other late writer — without it a reset committing between
    the shard scan and this write would let a zero-shard verdict from the old
    epoch flip the new epoch's fresh pending node to completed with no
    execution. ``execution_generation`` is the epoch the caller's scheduling
    pass read (``claim_shard_node`` scope); a mismatch skips the flip and the
    node stays pending for the next pass on the new epoch.
    """
    now_str = now_str or database_timestamp(datetime.now(UTC))
    current_generation = lock_job_mutation_and_read_generation(conn, job_id)
    if current_generation is None or current_generation != execution_generation:
        logger.info(
            "empty shard completion skipped (stale generation): job=%s node=%s expected=%s",
            job_id,
            node_key,
            execution_generation,
        )
        return False
    cursor = conn.execute(
        """
        update job_nodes
        set status='completed', error_message='', finished_at=%s
        where job_id=%s and node_key=%s and status in ('pending', 'ready', 'stale')
        """,
        (now_str, job_id, node_key),
    )
    if cursor.rowcount == 0:
        return False
    sync_job_status(conn, job_id)
    _pause_job_on_target_completion(conn, job_id, node_key, now_str)
    return True
