from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, StrictBool

from server.app.routes.instance_settings_core_contracts import (
    InstanceCleanupSettings,
    InstanceMonitoringSettings,
)
from server.app.routes.instance_settings_execution_contracts import (
    InstanceAgentClaimSettings,
    InstanceAgentEnqueueSettings,
    InstanceAgentWorkersSettings,
    InstanceResultUnpackSettings,
    InstanceResultValidateSettings,
    InstanceWorkflowsSettings,
)
from server.app.skills.skill_roots import SKILLS_ROOT_DISPLAY


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
    # Studio chat session retention in days (#1041); 0 = disabled (archived
    # / soft-deleted sessions are kept forever — the default). When enabled,
    # the studio chat retention sweep physically removes closed sessions
    # whose archive / soft-delete stamp is older than the window (messages
    # cascade with the row). Read fresh at sweep / list time, so edits take
    # effect without restart.
    studio_chat_retention_days: int = Field(ge=0, le=36500)
    # Studio chat ACP terminal grant fence (#1136); default off = pre-#921
    # direct execution (engines in auto permission mode never send
    # session/request_permission, so with the fence on no grant can ever be
    # minted and Bash is dead). True re-enables the #921 fence. Read fresh at
    # terminal/create; edits take effect without restart.
    studio_chat_terminal_grant_required: bool = False
    workflows: InstanceWorkflowsSettings
    agent_workers: InstanceAgentWorkersSettings
    # #509/#554: capacity knobs folded into the instance document;
    # restart-effective like the rest of the executor-runtime surface.
    agent_enqueue: InstanceAgentEnqueueSettings
    result_unpack: InstanceResultUnpackSettings
    result_validate: InstanceResultValidateSettings
    agent_claim: InstanceAgentClaimSettings
    # #989: document CSP compatibility switch (default False = strict nonce
    # script-src). True restores 'unsafe-inline' so published preview panels
    # relying on inline onclick= handlers keep working during migration.
    # Read at serve time (services/document_csp.py), no restart needed.
    # Strict: a security switch must not flip on a coerced "yes" / 1.
    csp_script_unsafe_inline: StrictBool


class InstanceSettingsResponse(InstanceSettingsDocument):
    # Read-only, server-injected: the on-disk skills root (single source of
    # truth in server.app.skills.skill_roots). Not part of the PUT document —
    # InstanceSettingsUpdate is extra="forbid" and rejects writes to it.
    skills_root: str = SKILLS_ROOT_DISPLAY


class InstanceSettingsUpdate(InstanceSettingsDocument): ...
