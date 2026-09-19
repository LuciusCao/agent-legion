"""Shard fan-out materialization under the execution-generation gate (#645 P3).

Split out of ``workflow_worker/shards.py`` for the file-size budget. The
claim pass's fan-out transaction (materialize shard rows, and complete the
node when the fan-out is empty) writes ``node_shards``/``job_nodes`` rows for
the epoch the pass evaluated; a reset committing mid-pass must not let the
stale verdict land.
"""

from __future__ import annotations

import logging

from server.app.db.connection import DatabaseConnection
from server.app.executors._lease_control import lock_job_mutation_and_read_generation
from server.app.executors._lease_shards import complete_empty_shard_node
from server.app.workflows.sharding import materialize_shards

logger = logging.getLogger(__name__)


def materialize_shards_guarded(
    conn: DatabaseConnection,
    job_id: str,
    node_key: str,
    inputs: list[dict],
    max_shards: int,
    execution_generation: int,
) -> None:
    """Materialize the fan-out (and complete an empty one) under the epoch gate.

    EXEC-GENERATION-001 (#645 P3): the job-mutation lock + generation CAS run
    FIRST in the caller's transaction — before any node_shards write — to keep
    the protocol's lock order (job-mutation advisory → row locks): the
    mutation side deletes shard rows while holding the same advisory lock, so
    taking it after the inserts would AB-BA. A stale epoch skips both the
    materialization and the empty-fan-out completion; the node stays pending
    and the next scheduling pass re-evaluates on the fresh epoch. Raises
    ``ShardLimitExceeded`` unchanged (the caller's config-failure arm has its
    own generation CAS).
    """
    current_generation = lock_job_mutation_and_read_generation(conn, job_id)
    if current_generation is None or current_generation != execution_generation:
        logger.info(
            "shard fan-out skipped (stale generation): job=%s node=%s expected=%s",
            job_id,
            node_key,
            execution_generation,
        )
        return
    if materialize_shards(conn, job_id, node_key, inputs, max_shards=max_shards) == 0:
        # Empty fan-out: zero shards aggregate to a completed node with empty
        # outputs; the reduce fan-in reads an empty array.
        complete_empty_shard_node(conn, job_id, node_key, execution_generation)
