"""Code stockpile gate: bound how many kind='code' requests pile up (issue #125).

The agent side throttles bundle production with the per-bucket stockpile
gate (``agent_stock``); the code side had none, so a poll pass with an
online code Worker enqueued every ready code node and flooded the broker
queue (14k queued rows in the 2026-08-18 prod incident). This gate is the
code-side counterpart, deliberately simpler: one global limit (no per
capability buckets) — the queued kind='code' stock must stay below a target
derived from the online code-Worker fleet's declared code capacity
(``max_code_concurrency`` sum x ``factor``), floored by ``min_stock`` and
capped at ``max_stock``. Over-target candidates stay pending and fall back
to the local code pool, which enforces its own capacity.
"""

from __future__ import annotations

import math
import time

from server.app.agent_control.registry import CODE_PROTOCOL_VERSION, ONLINE_THRESHOLD_SECONDS
from server.app.configuration.executor_knobs import CodeStockConfig as CodeStockConfig
from server.app.db.connection import DatabaseDsn
from server.app.db.transaction import read_connection


class CodeStockGate:
    """TTL-cached code stock levels; one shared gate per workflow worker.

    Advisory like the agent stock gate: a stale snapshot may lag fleet or
    queue changes by ``refresh_seconds``; the broker's one-active-request
    unique index stays the authoritative dedup.
    """

    def __init__(self, database_dsn: DatabaseDsn, config: CodeStockConfig) -> None:
        self._dsn = database_dsn
        self._config = config
        self._loaded_at = 0.0
        self._target = 0
        self._queued = 0

    def allows(self) -> bool:
        """True while the global queued code stock is below target."""
        if not self._config.enabled:
            return True
        now = time.monotonic()
        if now - self._loaded_at >= self._config.refresh_seconds:
            self._refresh()
            self._loaded_at = now
        return self._queued < self._target

    def _refresh(self) -> None:
        with read_connection(self._dsn) as conn:
            # Fleet capacity mirrors the online probe in code_dispatch
            # (has_online_code_worker): non-revoked, protocol v2, declared
            # code capacity, fresh heartbeat — minus the per
            # capability/workspace admission (this gate is global).
            capacity_row = conn.execute(
                "select coalesce(sum(max_code_concurrency), 0) as capacity from agent_workers"
                " where revoked_at is null and max_code_concurrency > 0"
                " and protocol_version >= %s"
                " and last_seen_at > now() - make_interval(secs => %s)",
                (CODE_PROTOCOL_VERSION, ONLINE_THRESHOLD_SECONDS),
            ).fetchone()
            queued_row = conn.execute(
                "select count(*) as n from agent_execution_requests"
                " where state = 'queued' and kind = 'code'"
            ).fetchone()
        capacity = int(capacity_row["capacity"]) if capacity_row is not None else 0
        queued = int(queued_row["n"]) if queued_row is not None else 0
        # Assign only after both reads succeeded: a failed refresh keeps the
        # previous snapshot instead of a half-applied one.
        self._target = min(
            max(math.ceil(capacity * self._config.factor), self._config.min_stock),
            self._config.max_stock,
        )
        self._queued = queued
