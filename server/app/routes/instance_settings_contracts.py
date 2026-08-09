from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from server.app.routes.instance_openclaw_contracts import InstanceOpenClawSettings


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
    workflows: InstanceWorkflowsSettings
    agent_workers: InstanceAgentWorkersSettings
    openclaw: InstanceOpenClawSettings


class InstanceSettingsResponse(InstanceSettingsDocument):
    pass


class InstanceSettingsUpdate(InstanceSettingsDocument):
    pass
