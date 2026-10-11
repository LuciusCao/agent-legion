"""Host-side recording of a worker's live capacity declaration.

The ``last_seen_at`` presence touch lives in ``worker_presence.py`` (#555 —
throttled writes); this module keeps only the declared-capacity sync.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any


def sync_declared_capacity(
    conn: Any,
    worker: Any,
    declared_max_concurrency: int | None,
    declared_max_code_concurrency: int | None = None,
    declared_node_limits: Mapping[str, int] | None = None,
) -> tuple[int, int, dict[str, int]]:
    """Return the enforced (agent, code, node limits), recording re-declarations.

    Workers re-declare live capacities on every claim poll; the Host records
    them so resizes take effect without re-registration (self-reported, then
    Host-enforced). The code pool defaults to 0 (agent-only Worker). #1158:
    the per-node limit map follows the same hot-sync contract — None (older
    Worker, field absent) preserves the stored map; an explicit map (empty
    included) replaces it, so console edits land on the next claim."""
    agent_pool = int(worker["max_concurrency"])
    code_pool = int(worker["max_code_concurrency"])
    if declared_max_concurrency is not None:
        agent_pool = declared_max_concurrency
    if declared_max_code_concurrency is not None:
        code_pool = declared_max_code_concurrency
    stored_limits: dict[str, int] = {
        str(key): int(value)
        for key, value in json.loads(worker["node_concurrency_limits_json"] or "{}").items()
    }
    node_limits = (
        {str(key): int(value) for key, value in declared_node_limits.items()}
        if declared_node_limits is not None
        else stored_limits
    )
    if (
        agent_pool != int(worker["max_concurrency"])
        or code_pool != int(worker["max_code_concurrency"])
        or node_limits != stored_limits
    ):
        conn.execute(
            "update agent_workers set max_concurrency=%s, max_code_concurrency=%s,"
            " node_concurrency_limits_json=%s where worker_id=%s",
            (agent_pool, code_pool, json.dumps(node_limits, sort_keys=True), worker["worker_id"]),
        )
    return agent_pool, code_pool, node_limits
