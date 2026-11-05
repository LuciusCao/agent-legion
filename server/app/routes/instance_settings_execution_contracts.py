from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from shared.code_contract import MIN_RESULT_ARCHIVE_BYTES


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
    # #786: byte budget for one custom node code version (the #628 env-only
    # decision is reversed); ge=1024 mirrors WorkflowsRuntimeConfig.
    # Restart-effective like the rest of the workflows block. No PUT default
    # (same rationale as max_items_per_run); the read path falls back to the
    # loaded runtime (env > 64KB code default) for legacy documents.
    node_code_max_bytes: int = Field(ge=1024)


class InstanceAgentWorkersSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # #1082: ge=MIN_RESULT_ARCHIVE_BYTES（1 KiB）mirrors
    # ExecutorRuntimeConfig — a ceiling that cannot hold even an empty
    # tar.gz (the failed-degrade metadata-only archive) is a misconfiguration.
    max_archive_bytes: int = Field(ge=MIN_RESULT_ARCHIVE_BYTES)
    min_protocol_version: int = Field(ge=1)
    # #521 peak-shaving gate on the result commit; restart-effective. 0 =
    # disabled (the kill-switch), ge=0 mirrors ExecutorRuntimeConfig.
    max_concurrent_result_commits: int = Field(ge=0)
    # #591 group-commit batching kill-switch (False = direct serial path,
    # 0.7.9 behavior); restart-effective.
    result_commit_batching: bool
    # #356 plan B: trust-reported artifacts' spot-check percent (0 = trust
    # everything, 100 = always verify — the pre-#356 behavior);
    # restart-effective.
    artifact_spot_check_percent: int = Field(ge=0, le=100)
    # #739: presigned GET TTL for the external artifact manifest's
    # download_url entries; restart-effective. Lower bound 60 keeps a mistyped
    # value from expiring between the manifest read and the first download;
    # upper bound mirrors S3's 7-day SigV4 presign ceiling.
    artifact_download_presign_ttl_seconds: int = Field(ge=60, le=604_800)


class InstanceAgentEnqueueSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # #509: Host enqueue-pool knobs; restart-effective. workers upper bound
    # 256 guards against a misconfiguration blowing up the thread count.
    workers: int = Field(ge=1, le=256)
    max_pending: int = Field(ge=1)


class InstanceResultUnpackSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # #554: result-unpack process pool size; restart-effective. 0 = auto
    # (min(4, cpu_count)), ge=0 mirrors ExecutorRuntimeConfig.
    workers: int = Field(ge=0, le=64)


class InstanceResultValidateSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # #569: result-validate process pool size; same semantics as
    # result_unpack (restart-effective, 0 = auto).
    workers: int = Field(ge=0, le=64)


class InstanceAgentClaimSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # #561: Worker presence-write throttle (#555 knob); restart-effective.
    # 0 = write on every claim/commit (restores the 0.7.5 behavior).
    # le=86400 mirrors AgentClaimConfig: keeps the value inside what
    # PostgreSQL make_interval(secs => ...) accepts (#565 codex review).
    worker_touch_interval_seconds: float = Field(ge=0, le=86400)
