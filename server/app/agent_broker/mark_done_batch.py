"""Batched request-close transaction for the #591 result group-commit.

``broker.mark_done`` keeps the direct serial path (the module it lives in
sits at its #401 frozen ceiling); this sister module owns the drained-wave
arm the batcher's writer thread runs — N guarded request closes inside ONE
``write_transaction``, then the per-request ``_notify_worker_released``
mirror post-commit. Per-item semantics match ``mark_done`` exactly (the
guarded SELECT ... FOR UPDATE re-runs per entry inside the shared
transaction; a None verdict is data); the win is one commit fsync and one
connection checkout for the whole wave slice.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from server.app.agent_broker.manifest_trim import MANIFEST_TRIM
from server.app.agent_broker.worker_presence import touch_worker
from server.app.db.transaction import write_transaction

if TYPE_CHECKING:
    from server.app.agent_broker.broker import AgentExecutionBroker

logger = logging.getLogger(__name__)


def mark_done_many(
    broker: AgentExecutionBroker,
    writes: list[tuple[str, str, str, Mapping[str, Any]]],
) -> list[str | None]:
    """Close a drained wave's requests in ONE transaction (see module)."""
    results: list[str | None] = []
    released: list[tuple[str, str]] = []
    with write_transaction(broker.database_dsn) as conn:
        for execution_id, worker_id, lease_id, outcome in writes:
            row = conn.execute(
                "select lease_id, agent_id, workspace_id from agent_execution_requests"
                " where execution_id=%s and worker_id=%s and lease_id=%s"
                " and state in ('claimed', 'reporting')"
                " for update",
                (execution_id, worker_id, lease_id),
            ).fetchone()
            if row is None:
                results.append(None)
                continue
            conn.execute(
                "update agent_execution_requests set state='done', outcome_json=%s,"
                " finished_at=current_timestamp, manifest_json="
                + MANIFEST_TRIM
                + " where execution_id=%s",
                (json.dumps(dict(outcome), ensure_ascii=False), execution_id),
            )
            touch_worker(conn, worker_id, min_interval_seconds=broker.touch_worker_interval_seconds)
            results.append(str(row["lease_id"]))
            released.append((worker_id, str(row["workspace_id"])))
    for worker_id, workspace_id in released:
        try:
            broker._notify_worker_released(worker_id, workspace_id)
        except Exception:
            # #204 broad-except audit: never-raise mirror (same contract the
            # direct path carries) — the request close is already committed;
            # a bus failure must not fail the batch's verdicts.
            logger.exception("worker-released notify failed for %s", worker_id)
    return results
