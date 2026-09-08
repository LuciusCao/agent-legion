"""Worker-level claim setup shared by the single and batch claim paths (#546).

Split from ``claim.py`` for the file budget: the Worker row lock, declared
capacity sync and live pool snapshot (the ``WorkerView`` build) are identical
for ``claim_in_transaction`` and ``claim_batch_in_transaction``. Everything
here runs inside the caller's write transaction.
"""

from __future__ import annotations

import json
from typing import Any

from server.app.agent_broker import agent_claim_compatibility
from server.app.agent_broker import claim_timing as _claim_timing
from server.app.agent_broker.agent_worker_capacity import sync_declared_capacity
from server.app.agent_broker.claim_scan import WorkerView

WORKER_SELECT_SQL = "select * from agent_workers where worker_id=%s for update"
ACTIVE_COUNT_SQL = (
    "select kind, count(*) as cnt from agent_execution_requests"
    " where worker_id=%s and state='claimed' group by kind"
)


def prepare_claim_view(
    conn: Any,
    worker_id: str,
    declared_max_concurrency: int | None = None,
    declared_max_code_concurrency: int | None = None,
    timer: _claim_timing.ClaimStageTimer | None = None,
) -> WorkerView:
    """Lock the Worker row, sync declared capacities, snapshot the live pools.

    ``timer`` (#448) closes the two worker_setup stages exactly where the
    pre-#546 inline code had them; None keeps this importable from tests that
    predate the instrumentation.
    """
    worker = conn.execute(WORKER_SELECT_SQL, (worker_id,)).fetchone()
    if timer is not None:
        timer.stage("worker_setup")
    if worker is None or worker["revoked_at"] is not None:
        raise ValueError("unknown or revoked Agent Worker")
    max_concurrency, max_code_concurrency = sync_declared_capacity(
        conn, worker, declared_max_concurrency, declared_max_code_concurrency
    )
    models = agent_claim_compatibility.worker_model_declarations(worker)
    active_rows = conn.execute(ACTIVE_COUNT_SQL, (worker_id,)).fetchall()
    if timer is not None:
        timer.stage("worker_setup")
    active_by_kind = {str(row["kind"]): int(row["cnt"]) for row in active_rows}
    return WorkerView(
        runtimes=set(json.loads(worker["runtimes_json"])),
        models=models,
        labels=json.loads(worker["labels_json"]),
        allowed_workspaces=set(json.loads(worker["allowed_workspaces_json"] or "[]")),
        agent_capacity=max_concurrency,
        agent_active=active_by_kind.get("agent", 0),
        code_capacity=max_code_concurrency,
        code_active=active_by_kind.get("code", 0),
        protocol_version=int(worker["protocol_version"]),
    )
