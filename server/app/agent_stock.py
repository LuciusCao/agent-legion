"""Agent stockpile gating: bound how many execution bundles are pre-built.

Bundles are built at enqueue time (~1s CPU each); without a cap every ready
agent node gets bundled, piling up thousands of queued requests far beyond
what Workers can drain. The stockpile gate throttles the production side:
per (workspace_id, agent_id) the queued stock must stay below a target
derived from the recent actual completion rate, floored by in-flight work
(cold start: Workers finishing their current tasks immediately find stock)
and ``min_stock``, capped by ``max_stock``. Candidates over target stay
pending and are restocked by a later pass as the stock drains.
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
    horizon_seconds: int = Field(default=300, ge=1)
    min_stock: int = Field(default=4, ge=0)
    max_stock: int = Field(default=500, ge=1)
    refresh_seconds: float = Field(default=30.0, gt=0)


@dataclass(frozen=True)
class StockBucket:
    """Live counters for one (workspace_id, agent_id) pair."""

    queued: int = 0
    claimed: int = 0
    done_in_window: int = 0

    def target(self, config: AgentStockConfig) -> int:
        """Stock target: completion rate projected over the horizon, floored
        by in-flight work plus ``min_stock`` and by ``min_stock`` itself,
        capped at ``max_stock``."""
        rate_target = math.ceil(
            self.done_in_window * config.horizon_seconds / config.window_seconds
        )
        return min(
            max(rate_target, self.claimed + config.min_stock, config.min_stock),
            config.max_stock,
        )


@dataclass(frozen=True)
class StockSnapshot:
    """Point-in-time stock levels; advisory only, refreshed per interval."""

    config: AgentStockConfig
    buckets: dict[tuple[str, str], StockBucket] = field(default_factory=dict)

    def allows(self, workspace_id: str, agent_id: str) -> bool:
        """True while queued stock for the pair is below its target."""
        bucket = self.buckets.get((workspace_id, agent_id), StockBucket())
        return bucket.queued < bucket.target(self.config)


def load_stock_snapshot(dsn: DatabaseDsn, config: AgentStockConfig) -> StockSnapshot:
    """Aggregate queued/claimed counts and the recent done rate per pair."""
    queued: dict[tuple[str, str], int] = {}
    claimed: dict[tuple[str, str], int] = {}
    done: dict[tuple[str, str], int] = {}
    with read_connection(dsn) as conn:
        rows = conn.execute(
            "select workspace_id, agent_id, state, count(*) as n"
            " from agent_execution_requests where state in ('queued', 'claimed')"
            " group by 1, 2, 3"
        ).fetchall()
        for row in rows:
            key = (str(row["workspace_id"]), str(row["agent_id"]))
            if row["state"] == "queued":
                queued[key] = int(row["n"])
            else:
                claimed[key] = int(row["n"])
        rows = conn.execute(
            "select workspace_id, agent_id, count(*) as n"
            " from agent_execution_requests"
            " where state = 'done' and finished_at > now() - make_interval(secs => ?)"
            " group by 1, 2",
            (config.window_seconds,),
        ).fetchall()
        for row in rows:
            done[(str(row["workspace_id"]), str(row["agent_id"]))] = int(row["n"])
    return StockSnapshot(
        config=config,
        buckets={
            key: StockBucket(
                queued=queued.get(key, 0),
                claimed=claimed.get(key, 0),
                done_in_window=done.get(key, 0),
            )
            for key in queued.keys() | claimed.keys() | done.keys()
        },
    )
