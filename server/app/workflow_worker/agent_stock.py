"""Agent stockpile gating: bound how many execution bundles are pre-built.

Bundles are built at enqueue time (~1s CPU each); without a cap every ready
agent node gets bundled, piling up thousands of queued requests far beyond
what Workers can drain. The stockpile gate throttles the production side:
per (workspace_id, agent_id) the queued stock must stay below a target
floored by the registered Worker capacity (cold start: idle Workers find
stock immediately, and the floor scales with the fleet instead of creeping
up from ``min_stock``), raised by the recent completion rate projected over
the horizon (fast tasks need a deeper buffer than one fleet-load), and
capped by ``max_stock``. Candidates over target stay pending and are
restocked by a later pass as the stock drains.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from pydantic import BaseModel, ConfigDict, Field

from server.app.db.connection import DatabaseDsn
from server.app.db.transaction import read_connection


class AgentStockConfig(BaseModel):
    """Tuning for the stockpile gate (``executor_runtime.agent_stock``)."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    window_seconds: int = Field(default=1800, ge=1)
    # Rate amplifier horizon: the done rate projected this far ahead deepens
    # stock for fast tasks / sudden bursts; the capacity floor covers the
    # baseline, so a few minutes of headroom is enough.
    horizon_seconds: int = Field(default=180, ge=1)
    min_stock: int = Field(default=4, ge=0)
    max_stock: int = Field(default=500, ge=1)
    refresh_seconds: float = Field(default=30.0, gt=0)
    # A Worker counts toward the capacity floor only when its last claim
    # poll is this recent (every poll touches last_seen_at, idle or not).
    worker_fresh_seconds: int = Field(default=120, ge=1)


@dataclass(frozen=True)
class StockBucket:
    """Live counters for one (workspace_id, agent_id) pair."""

    queued: int = 0
    done_in_window: int = 0


@dataclass(frozen=True)
class StockSnapshot:
    """Point-in-time stock levels; advisory only, refreshed per interval."""

    config: AgentStockConfig
    capacity: int = 0
    buckets: dict[tuple[str, str], StockBucket] = field(default_factory=dict)

    def target(self, bucket: StockBucket) -> int:
        """Stock target: completion rate projected over the horizon (the
        automatic depth multiplier for fast tasks), floored by the live
        Worker fleet capacity and by ``min_stock``, capped at ``max_stock``."""
        rate_target = math.ceil(
            bucket.done_in_window * self.config.horizon_seconds / self.config.window_seconds
        )
        return min(
            max(rate_target, self.capacity, self.config.min_stock),
            self.config.max_stock,
        )

    def allows(self, workspace_id: str, agent_id: str, extra: int = 0) -> bool:
        """True while queued stock for the pair is below its target.

        ``extra`` counts enqueue submissions made since this snapshot was
        loaded — they are not yet visible in ``queued``, and ignoring them
        would let a frozen snapshot over-release within its refresh window.
        """
        bucket = self.buckets.get((workspace_id, agent_id), StockBucket())
        return bucket.queued + extra < self.target(bucket)


def load_stock_snapshot(dsn: DatabaseDsn, config: AgentStockConfig) -> StockSnapshot:
    """Aggregate queued counts, the recent done rate, and the live fleet capacity."""
    queued: dict[tuple[str, str], int] = {}
    done: dict[tuple[str, str], int] = {}
    with read_connection(dsn) as conn:
        rows = conn.execute(
            "select workspace_id, agent_id, count(*) as n"
            " from agent_execution_requests where state = 'queued'"
            " group by 1, 2"
        ).fetchall()
        for row in rows:
            queued[(str(row["workspace_id"]), str(row["agent_id"]))] = int(row["n"])
        rows = conn.execute(
            "select workspace_id, agent_id, count(*) as n"
            " from agent_execution_requests"
            " where state = 'done' and finished_at > now() - make_interval(secs => %s)"
            " group by 1, 2",
            (config.window_seconds,),
        ).fetchall()
        for row in rows:
            done[(str(row["workspace_id"]), str(row["agent_id"]))] = int(row["n"])
        capacity_row = conn.execute(
            "select coalesce(sum(max_concurrency), 0) as capacity from agent_workers"
            " where revoked_at is null"
            " and last_seen_at > now() - make_interval(secs => %s)",
            (config.worker_fresh_seconds,),
        ).fetchone()
        capacity = int(capacity_row["capacity"]) if capacity_row is not None else 0
    return StockSnapshot(
        config=config,
        capacity=capacity,
        buckets={
            key: StockBucket(
                queued=queued.get(key, 0),
                done_in_window=done.get(key, 0),
            )
            for key in queued.keys() | done.keys()
        },
    )
