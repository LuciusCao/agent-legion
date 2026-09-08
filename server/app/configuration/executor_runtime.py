"""Typed ``executor_runtime`` settings model and startup validation.

Lives in the neutral configuration package so the settings layer never
imports the runtime packages (issue #188). The per-plane tuning knobs
(``AgentEnqueueConfig`` / ``AgentStockConfig`` / ``CodeStockConfig``) live
in ``executor_knobs``; this module aggregates them into the
``ExecutorRuntimeConfig`` document that ``server/app/settings.py`` embeds.
"""

from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from server.app.configuration.executor_knobs import (
    AgentEnqueueConfig,
    AgentStockConfig,
    CodeStockConfig,
)

logger = logging.getLogger(__name__)


class WorkflowsRuntimeConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # ``enabled`` retired (#385/#389): the gray-release switch drifted into a
    # de-facto product master switch with no legitimate off state; the
    # deployment-shape responsibility now lives on ``code_capacity``
    # (0 = pure-remote control plane) plus the sweeper escape hatch; stored
    # documents carrying the key are stripped at read time.
    # Feature gate for DB-backed custom workflow node codes (EXEC-CODE-002):
    # default on (self-hosted, workspace editors are team members, design
    # §7); disable via AGENT_LEGION_CUSTOM_NODES_ENABLED=0.
    custom_nodes_enabled: bool = True
    # Hard cap on one run's submitted items (#358 / #349 P0-1); 0 disables
    # the cap (not recommended). Instance-settings managed, restart-effective.
    max_items_per_run: int = Field(default=20_000, ge=0)


class AgentWorkersRuntimeConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Credentials retired with issue #35 (scoped-token-only registration).
    max_archive_bytes: int = Field(default=64 * 1024 * 1024, gt=0)
    min_protocol_version: int = Field(default=1, ge=1)


class CampaignsRuntimeConfig(BaseModel):
    """Campaign feeder/manifest knobs (#532 / #505, design §2.5): the #505
    CLI calibration as instance defaults; rows override watermark/batch_size.
    The feeder loop lands in PR-B; PR-A ships the validating API."""

    model_config = ConfigDict(extra="forbid")

    feed_interval_seconds: float = Field(default=10.0, gt=0)  # min gap between a campaign's batches
    feeder_tick_seconds: float = Field(default=2.0, gt=0)  # active-scan tick cadence
    # Replenishment trigger level, wide-set semantics (#349: total minus
    # completed/failed; paused/awaiting count).
    default_watermark: int = Field(default=30_000, ge=1)
    default_batch_size: int = Field(default=5_000, ge=1)
    # rerun/upgrade ceiling (5k slice ≈ 2.4s pass time vs 15s slow-pass).
    rerun_max_batch_size: int = Field(default=5_000, ge=1)
    max_active_per_workspace: int = Field(default=3, ge=1)  # pending+running campaigns
    manifest_inline_max_bytes: int = Field(default=262_144, ge=1)  # 256KB ≈ 2–3k items
    manifest_max_bytes: int = Field(default=52_428_800, ge=1)  # multipart upload ceiling


class ExecutorRuntimeConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    heartbeat_interval_seconds: float = Field(default=10, gt=0)
    lease_ttl_seconds: int = Field(default=90, ge=1)
    heartbeat_failure_threshold: int = Field(default=3, ge=1)
    cancellation_grace_seconds: int = Field(default=5, ge=0)
    # Local fallback execution capacity (#389): non-Agent-routed nodes run
    # here only when no remote code Worker is available; instance-settings
    # managed, restart-effective. 0 = pure remote mode (the host executes no
    # code nodes locally; requires an online code-capable Worker).
    code_capacity: int = Field(default=16, ge=0)
    sweeper_enabled: bool = True
    sweeper_interval_seconds: float = Field(default=5.0, gt=0)
    workflows: WorkflowsRuntimeConfig = Field(default_factory=WorkflowsRuntimeConfig)
    agent_workers: AgentWorkersRuntimeConfig = Field(default_factory=AgentWorkersRuntimeConfig)
    campaigns: CampaignsRuntimeConfig = Field(default_factory=CampaignsRuntimeConfig)
    agent_stock: AgentStockConfig = Field(default_factory=AgentStockConfig)
    code_stock: CodeStockConfig = Field(default_factory=CodeStockConfig)
    agent_enqueue: AgentEnqueueConfig = Field(default_factory=AgentEnqueueConfig)


class StartupValidationError(Exception):
    """Aggregated startup configuration errors (field paths + human-readable
    problems; secret values never included so messages log safely)."""

    def __init__(self, fields: list[tuple[str, str]]) -> None:
        self.fields = fields
        super().__init__(self._format(fields))

    @staticmethod
    def _format(fields: list[tuple[str, str]]) -> str:
        return "Startup validation failed: " + "; ".join(
            f"{location}: {message}" for location, message in fields
        )


def _resolve_executable(value: str) -> Path | None:
    expanded = os.path.expanduser(value)
    if os.sep in expanded or (os.altsep and os.altsep in expanded):
        path = Path(expanded)
        return path if path.is_file() and os.access(path, os.X_OK) else None
    found = shutil.which(expanded)
    return Path(found) if found else None


def validate_runtime(runtime: ExecutorRuntimeConfig, config: dict[str, Any]) -> None:
    """Validate enabled runtime dependencies at startup: business integrations
    retired with the legacy workflows (external endpoints live on instance
    connections, injected at dispatch), the pi precheck with the executor
    concept (P-0.5, v47; agents preflight Worker-side), openclaw with #75 —
    kept as the seam for future checks."""
    errors: list[tuple[str, str]] = []
    if errors:
        raise StartupValidationError(errors)
