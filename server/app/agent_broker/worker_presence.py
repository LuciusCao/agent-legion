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

#1320 adds the read-side sibling: an online-fleet probe for the material
ETag gate, used by the code enqueue path to tell a v6-capable fleet from a
pre-v6 one before shipping a gated request.
"""

from __future__ import annotations

from typing import Any

from server.app.agent_control.registry import ONLINE_THRESHOLD_SECONDS
from server.app.db.dialect import ConnectSource
from server.app.db.transaction import read_connection
from shared.protocol import MATERIAL_ETAG_PROTOCOL_VERSION

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


def has_online_etag_capable_code_worker(database_dsn: ConnectSource, workspace_id: str) -> bool:
    """#1320: True when an online >= v6 code Worker can claim in *workspace_id*.

    Mirrors ``code_dispatch.has_online_code_worker`` (code capacity + workspace
    ACL, fresh heartbeat) with the protocol floor raised to the v6 material-ETag
    protocol: a pre-v6 Worker skips gate-hit requests at admission forever, so
    against a pre-v6-only fleet the enqueue path must not ship them at all.
    """
    with read_connection(database_dsn) as conn:
        row = conn.execute(
            "select 1 from agent_workers where revoked_at is null"
            " and max_code_concurrency > 0 and protocol_version >= %s"
            " and last_seen_at > now() - make_interval(secs => %s)"
            " and (allowed_workspaces_json::jsonb = '[]'::jsonb or allowed_workspaces_json::jsonb @> jsonb_build_array(%s::text))"
            " limit 1",
            (MATERIAL_ETAG_PROTOCOL_VERSION, ONLINE_THRESHOLD_SECONDS, workspace_id),
        ).fetchone()
    return row is not None
