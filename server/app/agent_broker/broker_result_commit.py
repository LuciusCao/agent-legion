"""Result-commit request reads and optional group-commit routing."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, cast

from server.app.db.transaction import read_connection

if TYPE_CHECKING:
    from server.app.agent_broker.broker import AgentExecutionBroker


def claimed_payload(
    broker: AgentExecutionBroker, execution_id: str, worker_id: str
) -> dict[str, Any] | None:
    with read_connection(broker.database_dsn) as conn:
        row = conn.execute(
            "select manifest_json, lease_id, job_id, node_key from agent_execution_requests"
            " where execution_id=%s and worker_id=%s and state in ('claimed', 'reporting')",
            (execution_id, worker_id),
        ).fetchone()
    if row is None:
        return None
    return {
        "manifest": json.loads(row["manifest_json"]),
        "lease_id": row["lease_id"],
        "job_id": row["job_id"],
        "node_key": row["node_key"],
    }


def cancelled_code_executions(broker: AgentExecutionBroker, worker_id: str) -> list[str]:
    """Claimed kind='code' executions whose job left the runnable set.

    Backs the heartbeat cancel body (batch 2, design §7.5): the Worker
    kills the process group for each returned execution_id. Only
    'claimed' rows matter — 'reporting' executions already exited.
    """
    with read_connection(broker.database_dsn) as conn:
        rows = conn.execute(
            "select r.execution_id from agent_execution_requests r"
            " join jobs j on j.id=r.job_id"
            " where r.worker_id=%s and r.kind='code' and r.state='claimed'"
            " and (j.execution_paused=1 or j.status not in ('queued', 'running'))",
            (worker_id,),
        ).fetchall()
    return [str(row["execution_id"]) for row in rows]


def mark_done(
    broker: AgentExecutionBroker,
    execution_id: str,
    worker_id: str,
    lease_id: str,
    outcome: Mapping[str, Any],
) -> str | None:
    """Route a terminal request write through the optional group committer."""
    # #591 group-commit：batcher 在位时终态写停靠写线程队列、与完成波
    # 其余成员合批；None（关闭/测试）保持直连路径。
    if broker.result_batcher is not None:
        # direct = the post-stop fallback (#591 C6): a racing producer
        # must not park on the drained queue.
        direct = lambda: broker._mark_done_direct(  # noqa: E731
            execution_id, worker_id, lease_id, outcome
        )
        return cast(
            "str | None",
            broker.result_batcher.submit(
                "mark_done", (execution_id, worker_id, lease_id, outcome), direct=direct
            ),
        )
    return broker._mark_done_direct(execution_id, worker_id, lease_id, outcome)
