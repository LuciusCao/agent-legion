"""Executor runtime tuning knobs shared by settings and the runtime packages.

``executor_runtime`` aggregates tuning for all three execution planes
(local executor, agent broker, workflow worker). The knob models live in
this neutral configuration module so the config layer never imports the
runtime packages (issue #188: settings -> executors -> agent_broker /
workflow_worker was an inverted dependency); the consumers
(``agent_broker``, ``workflow_worker``, ``executors``) import from here
instead. ``ResultUnpackConfig`` (#552/#554) sizes the Host-side result
unpack process pool.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class AgentEnqueueConfig(BaseModel):
    """Enqueue-pool tuning (``executor_runtime.agent_enqueue``); each closure
    is ~1s of mostly-IO work, so throughput scales with ``workers``."""

    model_config = ConfigDict(extra="forbid")

    # #546 hotfix 同批：备货池实测跟不上（16 workers × ~1s/单 ≈ 960/分钟，
    # batch claim 把消费侧抬到数千/分钟后供给侧成为瓶颈），默认 48。
    # le=256 与实例设置契约（#509）对齐，防误配打爆线程数。
    workers: int = Field(default=48, ge=1, le=256)
    max_pending: int = Field(default=1024, ge=1)


class ResultUnpackConfig(BaseModel):
    """Result-unpack process pool size (``executor_runtime.result_unpack``,
    #552/#554). 0 = auto (min(4, cpu_count)); the pool is created lazily on
    the first result commit, so startup hydration always lands before pool
    creation (restart-effective)."""

    model_config = ConfigDict(extra="forbid")

    workers: int = Field(default=0, ge=0, le=64)


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
