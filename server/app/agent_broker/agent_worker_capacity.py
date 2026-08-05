"""Host-side recording of a worker's live capacity declaration."""

from __future__ import annotations

from typing import Any


def touch_worker(conn: Any, worker_id: str) -> None:
    conn.execute(
        "update agent_workers set last_seen_at=current_timestamp where worker_id=?",
        (worker_id,),
    )


def sync_declared_capacity(conn: Any, worker: Any, declared_max_concurrency: int | None) -> int:
    """Return the enforced machine-wide capacity, recording re-declarations.

    The worker re-declares its live ``max_concurrency`` on every claim poll;
    the Host records it so dynamic resizes take effect without a
    re-registration. Same trust level as the registration-time declaration:
    the value is self-reported, then Host-enforced.
    """
    max_concurrency = int(worker["max_concurrency"])
    if declared_max_concurrency is not None and declared_max_concurrency != max_concurrency:
        conn.execute(
            "update agent_workers set max_concurrency=? where worker_id=?",
            (declared_max_concurrency, worker["worker_id"]),
        )
        return declared_max_concurrency
    return max_concurrency
