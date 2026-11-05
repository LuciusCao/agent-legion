from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class InstanceCleanupSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    log_retention_days: int = Field(ge=1)
    run_dir_retention_days: int = Field(ge=1)
    interval_seconds: int = Field(ge=1)


class InstanceMonitoringSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sample_interval_seconds: float = Field(gt=0)
    retention_days: int = Field(ge=1)
