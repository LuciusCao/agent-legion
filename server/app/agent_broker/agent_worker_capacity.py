"""Host-side recording of a worker's live capacity declaration."""

from __future__ import annotations

from typing import Any


def touch_worker(conn: Any, worker_id: str) -> None:
    conn.execute(
        "update agent_workers set last_seen_at=current_timestamp where worker_id=%s",
        (worker_id,),
    )


def sync_declared_capacity(
    conn: Any,
    worker: Any,
    declared_max_concurrency: int | None,
    declared_max_code_concurrency: int | None = None,
) -> tuple[int, int]:
    """Return the enforced (agent, code) capacities, recording re-declarations.

    Workers re-declare live capacities on every claim poll; the Host records
    them so resizes take effect without re-registration (self-reported, then
    Host-enforced). The code pool defaults to 0 (agent-only Worker)."""
    agent_pool = int(worker["max_concurrency"])
    code_pool = int(worker["max_code_concurrency"])
    if declared_max_concurrency is not None:
        agent_pool = declared_max_concurrency
    if declared_max_code_concurrency is not None:
        code_pool = declared_max_code_concurrency
    if agent_pool != int(worker["max_concurrency"]) or code_pool != int(
        worker["max_code_concurrency"]
    ):
        conn.execute(
            "update agent_workers set max_concurrency=%s, max_code_concurrency=%s"
            " where worker_id=%s",
            (agent_pool, code_pool, worker["worker_id"]),
        )
    return agent_pool, code_pool
