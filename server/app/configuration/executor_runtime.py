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

    # Default on: matches the retired tracked workflow.yaml value
    # (workflows.enabled: true); tunable via the DB instance settings document.
    enabled: bool = True
    # Feature gate for DB-backed custom workflow node codes (EXEC-CODE-002).
    # Default on in this phase: self-hosted, workspace editors are all team
    # members (design §7 trust assumption). Disable via
    # AGENT_LEGION_CUSTOM_NODES_ENABLED=0.
    custom_nodes_enabled: bool = True
    # Hard cap on one run's submitted items (#358 / #349 P0-1): a single
    # POST /runs inserts every item in one transaction, so oversized runs
    # blow memory and transaction length before the first job even executes.
    # Default sits on the batched-submission baseline ceiling (2×10^4 items
    # per run); 0 disables the cap (not recommended). Instance-settings
    # managed, takes effect on restart.
    max_items_per_run: int = Field(default=20_000, ge=0)


class AgentWorkersRuntimeConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # The global register token (register_token / register_token_file) was
    # retired with issue #35: registration is scoped-token-only, so this
    # section no longer carries any credential.
    max_archive_bytes: int = Field(default=64 * 1024 * 1024, gt=0)
    min_protocol_version: int = Field(default=1, ge=1)


class ExecutorRuntimeConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    heartbeat_interval_seconds: float = Field(default=10, gt=0)
    lease_ttl_seconds: int = Field(default=90, ge=1)
    heartbeat_failure_threshold: int = Field(default=3, ge=1)
    cancellation_grace_seconds: int = Field(default=5, ge=0)
    # Implicit single code pool capacity (P-0.5): non-Agent-routed nodes all
    # claim from this pool. Instance-settings managed; takes effect on
    # restart (no hot reload).
    code_capacity: int = Field(default=16, gt=0)
    sweeper_enabled: bool = True
    sweeper_interval_seconds: float = Field(default=5.0, gt=0)
    workflows: WorkflowsRuntimeConfig = Field(default_factory=WorkflowsRuntimeConfig)
    agent_workers: AgentWorkersRuntimeConfig = Field(default_factory=AgentWorkersRuntimeConfig)
    agent_stock: AgentStockConfig = Field(default_factory=AgentStockConfig)
    code_stock: CodeStockConfig = Field(default_factory=CodeStockConfig)
    agent_enqueue: AgentEnqueueConfig = Field(default_factory=AgentEnqueueConfig)


class StartupValidationError(Exception):
    """Aggregated startup configuration errors.

    Diagnostics list field paths and human-readable problems; secret values are
    never included so messages can be logged safely.
    """

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
    """Validate enabled runtime dependencies at startup.

    Business integrations (CMS credentials, ASR machine paths) retired with the
    legacy business workflows: external service endpoints/credentials live on
    instance-level connections and are injected into node config at dispatch
    time, so startup has nothing to pre-check for them. The pi executor
    precheck retired with the executor concept (P-0.5, schema v47): agent
    runtimes are preflighted on the Agent Worker side. The ``openclaw`` block
    retired with the openclaw runtime (#75). Kept as the startup-validation
    seam for future checks.
    """
    errors: list[tuple[str, str]] = []
    if errors:
        raise StartupValidationError(errors)
