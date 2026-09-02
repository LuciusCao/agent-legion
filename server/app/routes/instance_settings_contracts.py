from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

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

    # ``enabled`` retired (#385/#389): it drifted from a feature flag into a
    # de-facto product master switch; the deployment-shape responsibility
    # moved to code_capacity (0 = pure-remote control plane) and the sweeper
    # escape hatch. Stored documents still carrying the key are stripped at
    # read time (instance_settings._strip_retired_keys).
    # Hard cap on one run's submitted items (#358 / #349 P0-1); 0 disables.
    # Restart-effective like the rest of the workflows block.
    # No PUT default: a full-document PUT that omits the field must not
    # silently re-arm a disabled (0) cap. The read path merges the code
    # default for legacy documents.
    max_items_per_run: int = Field(ge=0)


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
    # Local fallback execution capacity (#389); restart-effective. 0 = pure
    # remote mode: no local code execution, only remote code Workers.
    code_capacity: int = Field(ge=0)
    # Materials TTL in days (design §10); 0 = disabled. Read fresh from the
    # DB at material completion time, so edits take effect without restart.
    # Upper bound ~100 years: larger values overflow now() + make_interval.
    materials_ttl_days: int = Field(ge=0, le=36500)
    # Execution-plane row retention in days (issue #354); 0 = disabled
    # (nothing is ever deleted — the safe default). When enabled, the
    # execution-retention sweep deletes terminal ``agent_execution_requests``
    # / ``executor_leases`` / ``node_run_token_usage`` rows older than the
    # window, in small batches. Read fresh from the DB at sweep time, so
    # edits take effect without restart.
    execution_retention_days: int = Field(ge=0, le=36500)
    workflows: InstanceWorkflowsSettings
    agent_workers: InstanceAgentWorkersSettings


class InstanceSettingsResponse(InstanceSettingsDocument):
    # Read-only, server-injected: the on-disk skills root (single source of
    # truth in server.app.skills.skill_roots). Not part of the PUT document —
    # InstanceSettingsUpdate is extra="forbid" and rejects writes to it.
    skills_root: str = SKILLS_ROOT_DISPLAY


class InstanceSettingsUpdate(InstanceSettingsDocument):
    pass
