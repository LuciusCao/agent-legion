from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator

from server.app.agent_broker.dispatch_pool import AgentEnqueueConfig
from server.app.workflow_worker.agent_stock import AgentStockConfig
from server.app.workflow_worker.code_stock import CodeStockConfig

logger = logging.getLogger(__name__)


class PiRuntimeConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    flavor: Literal["pi", "velites"] = "pi"  # runtime: pi 的遗留实现选择层（§9）；非法值 fail-fast
    binary: str = Field(default="pi", validate_default=True)
    provider: str = ""
    model: str = ""
    thinking: str = ""
    timeout_seconds: int = Field(default=600, ge=1)
    cancellation_grace_seconds: int = Field(default=5, ge=0)
    environment: dict[str, str] = Field(default_factory=dict)
    velites_no_sandbox: bool = False  # 逃生门：velites 下传 --no-sandbox（沙箱降级免发版）

    @field_validator("binary")
    @classmethod
    def _flavor_binary(cls, value: str, info: ValidationInfo) -> str:
        return "velites" if info.data.get("flavor") == "velites" and value == "pi" else value


class OpenClawSkillSafetyRepo(BaseModel):
    """One skill checkout the OpenClaw runner may force-restore before a run.

    Only the path is declared here; the restore ref is pinned by the DB
    ``skill_lock`` document (config governance G3, single source of truth).
    A ``ref`` key is rejected as an extra field.
    """

    model_config = ConfigDict(extra="forbid")

    path: str = Field(min_length=1)


class OpenClawSkillSafetyRuntimeConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    repos: list[OpenClawSkillSafetyRepo] = Field(default_factory=list)


class OpenClawRuntimeConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    command_template: tuple[str, ...] = Field(min_length=1)
    cwd: str = "."
    timeout_seconds: int = Field(default=600, ge=1)
    cancellation_grace_seconds: int = Field(default=5, ge=0)
    isolated_workspace_root: str = ""
    skill_safety: OpenClawSkillSafetyRuntimeConfig = Field(
        default_factory=OpenClawSkillSafetyRuntimeConfig
    )


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
    pi: PiRuntimeConfig = Field(default_factory=PiRuntimeConfig)


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
    openclaw: OpenClawRuntimeConfig = Field(
        default_factory=lambda: OpenClawRuntimeConfig(command_template=("openclaw",))
    )
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


def _expand(value: str) -> Path:
    return Path(os.path.expanduser(value))


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
    runtimes are preflighted on the Agent Worker side.
    """
    errors: list[tuple[str, str]] = []

    openclaw_cwd = str(runtime.openclaw.cwd or ".")
    if not _expand(openclaw_cwd).is_dir():
        errors.append(("openclaw.cwd", "openclaw working directory does not exist"))

    if errors:
        raise StartupValidationError(errors)
