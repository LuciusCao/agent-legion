"""Agent stockpile gating: bound how many execution bundles are pre-built.

Bundles are built at enqueue time (~1s CPU each); without a cap every ready
agent node gets bundled, piling up thousands of queued requests far beyond
what Workers can drain. The stockpile gate throttles the production side:
per (workspace_id, agent_id) the queued stock must stay below a target
raised by the recent completion rate projected over the horizon (fast tasks
need a deeper buffer than one fleet-load), floored by ``min_stock``, and
capped by ``max_stock``. Candidates over target stay pending and are
restocked by a later pass as the stock drains.

The live Worker fleet capacity is a single shared floor pool, NOT a
per-bucket floor: bucket floors consume the pool in workflow order, with
deeper (later) DAG nodes drawing first so in-flight jobs finish before new
work is stocked upstream. A bucket's effective floor is the fleet capacity
minus the queued stock of every bucket at the same or deeper tier, so the
fleet-wide queued total stays bounded by the fleet capacity no matter how
many agent buckets exist (regression: the per-bucket floor multiplied the
fleet capacity by the bucket count, e.g. 5 buckets x 128 = 640 in-flight
bundles against 128 real slots).

The snapshot loader lives in ``agent_stock_snapshot`` (file-size budget);
this module keeps the gate math only.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from pydantic import BaseModel, ConfigDict, Field


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


# Buckets missing from the priority map (never seen on a workflow revision)
# draw from the pool after every known bucket: they are treated as one tier
# below the shallowest known node.
UNKNOWN_PRIORITY = -1


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
    # Priority tier per bucket = topological depth of the agent's node(s) in
    # the workspace workflow DAG. Deeper (later) nodes draw the shared
    # capacity floor first, so in-flight jobs drain before upstream stock
    # piles up. A bucket may span several nodes/workflows; the max wins.
    priorities: dict[tuple[str, str], int] = field(default_factory=dict)

    def target(self, key: tuple[str, str]) -> int:
        """Stock target for one (workspace_id, agent_id) pair.

        Completion rate projected over the horizon (the automatic depth
        multiplier for fast tasks), floored by ``min_stock`` and by the
        bucket's share of the shared fleet-capacity pool, capped at
        ``max_stock``. The pool share is the fleet capacity minus the queued
        stock of every bucket at the same or deeper priority tier: deeper
        buckets see the full pool, shallower buckets see what deeper work
        leaves behind, and unknown pairs draw last.
        """
        bucket = self.buckets.get(key, StockBucket())
        rate_target = math.ceil(
            bucket.done_in_window * self.config.horizon_seconds / self.config.window_seconds
        )
        own_tier = self.priorities.get(key, UNKNOWN_PRIORITY)
        pool_floor = self.capacity - sum(
            other.queued
            for other_key, other in self.buckets.items()
            if other_key != key and self.priorities.get(other_key, UNKNOWN_PRIORITY) >= own_tier
        )
        return min(
            max(rate_target, pool_floor, self.config.min_stock),
            self.config.max_stock,
        )

    def allows(self, workspace_id: str, agent_id: str, extra: int = 0) -> bool:
        """True while queued stock for the pair is below its target.

        ``extra`` counts enqueue submissions made since this snapshot was
        loaded — they are not yet visible in ``queued``, and ignoring them
        would let a frozen snapshot over-release within its refresh window.
        """
        key = (workspace_id, agent_id)
        bucket = self.buckets.get(key, StockBucket())
        return bucket.queued + extra < self.target(key)
