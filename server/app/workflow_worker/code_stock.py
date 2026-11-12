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

#1320 (B): requests parked by the v6 material-ETag claim gate
(``material_claim_gate``) are unclaimable while the fleet has NO online
>= v6 code Worker (claim admission skips them for pre-v6 Workers forever);
counting them would let an unclaimable backlog starve claimable requests,
so the refresh subtracts them. The exclusion is fleet-shaped, not per-row:
the moment any >= v6 code Worker is online the gated rows drain normally
and count again. Enqueue-side (#1320 A4 in code_claim) keeps NEW gated
requests off the queue in that fleet shape; this exclusion covers rows
queued before the fleet lost its last v6 Worker.
"""

from __future__ import annotations

import math
import time

from server.app.agent_control.registry import CODE_PROTOCOL_VERSION, ONLINE_THRESHOLD_SECONDS
from server.app.configuration.executor_knobs import CodeStockConfig as CodeStockConfig
from server.app.db.dialect import ConnectSource
from server.app.db.transaction import read_connection
from server.app.jobs.queries.material_claim_gate import requires_etag_protocol_sql
from shared.protocol import MATERIAL_ETAG_PROTOCOL_VERSION

# Queued code requests whose job input hits the v6 ETag gate predicate
# (material or any bundle member). ``jobs.input_json`` is JSON *text* with
# legacy NULL/'' rows, so the parse is CASE-guarded: only object-shaped
# documents are inspected. Runs only while the fleet has no online >= v6
# code Worker (a degraded shape; the probe is per refresh TTL).
_GATED_QUEUED_SQL = (
    "select count(*) as n from agent_execution_requests aer join jobs j on j.id = aer.job_id"
    " cross join lateral (select case when coalesce(j.input_json, '') like '{%'"
    " then j.input_json::jsonb else '{}'::jsonb end as doc) ij"
    " where aer.state = 'queued' and aer.kind = 'code' and ("
    " (ij.doc ->> 'type' = 'material' and exists (select 1 from materials mat"
    " where mat.id = ij.doc ->> 'material_id' and mat.workspace_id = aer.workspace_id"
    f" and {requires_etag_protocol_sql('mat')}))"
    " or (ij.doc ->> 'type' = 'bundle' and exists (select 1 from material_bundle_members m"
    " join materials mat on mat.id = m.material_id"
    " where m.bundle_id = ij.doc ->> 'bundle_id' and mat.workspace_id = aer.workspace_id"
    f" and {requires_etag_protocol_sql('mat')})))"
)


class CodeStockGate:
    """TTL-cached code stock levels; one shared gate per workflow worker.

    Advisory like the agent stock gate: a stale snapshot may lag fleet or
    queue changes by ``refresh_seconds``; the broker's one-active-request
    unique index stays the authoritative dedup.
    """

    def __init__(self, database_dsn: ConnectSource, config: CodeStockConfig) -> None:
        self._dsn = database_dsn
        self._config = config
        self._loaded_at = 0.0
        self._target = 0
        self._queued = 0

    def allows(self) -> bool:
        """True while the global queued code stock is below target."""
        if not self._config.enabled:
            return True
        self._ensure_fresh()
        return self._queued < self._target

    def pass_budget(self) -> int | None:
        """Requests THIS poll pass may still enqueue; None when the gate is off.

        #401: within one pass ``allows()`` cannot see the shards this pass
        just submitted (enqueue pool is asynchronous, the gate counts only
        committed rows). The shard fan-out caps its own submissions at this
        budget — same target/clamps/TTL as ``allows()``; the next pass
        continues where this one stopped.
        """
        if not self._config.enabled:
            return None
        self._ensure_fresh()
        return max(self._target - self._queued, 0)

    def _ensure_fresh(self) -> None:
        """Refresh the snapshot when the TTL expired (enabled gate only)."""
        now = time.monotonic()
        if now - self._loaded_at >= self._config.refresh_seconds:
            self._refresh()
            self._loaded_at = now

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
            # #1320 (B): without an online >= v6 code Worker, requests parked
            # by the material ETag gate can never be claimed — exclude them
            # from the stock so the backlog cannot starve claimable requests.
            v6_online = conn.execute(
                "select 1 from agent_workers where revoked_at is null and max_code_concurrency > 0 and protocol_version >= %s and last_seen_at > now() - make_interval(secs => %s) limit 1",
                (MATERIAL_ETAG_PROTOCOL_VERSION, ONLINE_THRESHOLD_SECONDS),
            ).fetchone()
            gated = 0
            if (
                v6_online is None
                and (grow := conn.execute(_GATED_QUEUED_SQL).fetchone()) is not None
            ):
                gated = int(grow["n"])
        capacity = int(capacity_row["capacity"]) if capacity_row is not None else 0
        queued = max(int(queued_row["n"]) - gated, 0) if queued_row is not None else 0
        # Assign only after both reads succeeded: a failed refresh keeps the
        # previous snapshot instead of a half-applied one.
        self._target = min(
            max(math.ceil(capacity * self._config.factor), self._config.min_stock),
            self._config.max_stock,
        )
        self._queued = queued
