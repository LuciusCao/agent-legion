from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from server.app.routes.instance_openclaw_contracts import InstanceOpenClawSettings
from server.app.skills.skill_roots import SKILLS_ROOT_DISPLAY


class InstanceCleanupSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    log_retention_days: int = Field(ge=1)
    run_dir_retention_days: int = Field(ge=1)
    interval_seconds: int = Field(ge=1)


class InstanceMonitoringSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sample_interval_seconds: float = Field(gt=0)
    retention_days: int = Field(ge=1)


class InstanceWorkflowsSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool


class InstanceAgentWorkersSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_archive_bytes: int = Field(gt=0)
    min_protocol_version: int = Field(ge=1)


class InstanceSettingsDocument(BaseModel):
    """Full instance settings document; constraints mirror ExecutorRuntimeConfig."""

    model_config = ConfigDict(extra="forbid")

    cleanup: InstanceCleanupSettings
    monitoring: InstanceMonitoringSettings
    heartbeat_interval_seconds: float = Field(gt=0)
    lease_ttl_seconds: int = Field(ge=1)
    heartbeat_failure_threshold: int = Field(ge=1)
    sweeper_enabled: bool
    sweeper_interval_seconds: float = Field(gt=0)
    # Implicit single code pool capacity (P-0.5); restart-effective.
    code_capacity: int = Field(gt=0)
    # Materials TTL in days (design §10); 0 = disabled. Read fresh from the
    # DB at material completion time, so edits take effect without restart.
    # Upper bound ~100 years: larger values overflow now() + make_interval.
    materials_ttl_days: int = Field(ge=0, le=36500)
    workflows: InstanceWorkflowsSettings
    agent_workers: InstanceAgentWorkersSettings
    openclaw: InstanceOpenClawSettings


class InstanceSettingsResponse(InstanceSettingsDocument):
    # Read-only, server-injected: the on-disk skills root (single source of
    # truth in server.app.skills.skill_roots). Not part of the PUT document —
    # InstanceSettingsUpdate is extra="forbid" and rejects writes to it.
    skills_root: str = SKILLS_ROOT_DISPLAY


class InstanceSettingsUpdate(InstanceSettingsDocument):
    pass
