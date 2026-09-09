"""Throttled ``agent_workers.last_seen_at`` maintenance (#555).

Claim promote and ``mark_done`` used to rewrite ``last_seen_at`` on every
call: with a small fleet, every claim and every result commit serialized on
the same few hot rows (measured as transactionid queueing on
``update agent_workers`` in #555). Liveness does not need that fidelity —
the heartbeat channel and the authenticate-path ``WorkerLiveness`` (#88,
one write per 10s per Worker) already keep the column well inside the 30s
online threshold — so the claim/commit touches write only when the
persisted value is older than ``min_interval_seconds``.

The throttle predicate lives in the UPDATE itself: a throttled touch
matches no row and takes no row lock, which is what lets ``mark_done``
skip the hot-row lock entirely (claim paths hold the worker row anyway via
``prepare_claim_view``). ``min_interval_seconds=0`` keeps the pre-#555
always-write semantics (heartbeat paths); within one transaction a second
touch is a no-op either way (``current_timestamp`` is transaction time).
"""

from __future__ import annotations

from typing import Any

# Default throttle for the claim/commit touches; mirrored by the
# ``executor_runtime.agent_claim.worker_touch_interval_seconds`` knob
# (executor_knobs.py — the configuration package must not import the
# runtime packages, #188, so the literal is duplicated by design).
DEFAULT_TOUCH_INTERVAL_SECONDS = 30.0


def touch_worker(conn: Any, worker_id: str, *, min_interval_seconds: float = 0.0) -> None:
    conn.execute(
        "update agent_workers set last_seen_at=current_timestamp where worker_id=%s"
        " and (last_seen_at is null or last_seen_at < current_timestamp - make_interval(secs => %s))",
        (worker_id, min_interval_seconds),
    )
