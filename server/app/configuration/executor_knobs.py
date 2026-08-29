"""Executor runtime tuning knobs shared by settings and the runtime packages.

``executor_runtime`` aggregates tuning for all three execution planes
(local executor, agent broker, workflow worker). The knob models live in
this neutral configuration module so the config layer never imports the
runtime packages (issue #188: settings -> executors -> agent_broker /
workflow_worker was an inverted dependency); the consumers
(``agent_broker``, ``workflow_worker``, ``executors``) import from here
instead.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class AgentEnqueueConfig(BaseModel):
    """Enqueue-pool tuning (``executor_runtime.agent_enqueue``); each closure
    is ~1s of mostly-IO work, so throughput scales with ``workers``."""

    model_config = ConfigDict(extra="forbid")

    workers: int = Field(default=16, ge=1)
    max_pending: int = Field(default=1024, ge=1)


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


class CodeStockConfig(BaseModel):
    """Tuning for the code stockpile gate (``executor_runtime.code_stock``)."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    # Fleet-capacity amplifier: a factor above 1 keeps a claimable buffer
    # ahead of the fleet so Workers never poll a dry queue between passes.
    factor: float = Field(default=1.5, gt=0)
    min_stock: int = Field(default=8, ge=0)
    max_stock: int = Field(default=256, ge=1)
    refresh_seconds: float = Field(default=5.0, gt=0)
