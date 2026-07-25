from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

ExecutionStatus = Literal["completed", "failed", "cancelled"]


@dataclass(frozen=True)
class ExecutionContext:
    execution_id: str
    lease_id: str
    node_run_id: int
    executor_id: str
    workspace_id: str
    job_id: str
    workflow_key: str
    node_key: str
    capability: str
    workspace: Mapping[str, object]
    job: Mapping[str, object]
    job_dir: Path
    log_path: Path
    inputs: tuple[str, ...]
    expected_outputs: tuple[str, ...]
    runtime: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class ExecutionResult:
    status: ExecutionStatus
    exit_code: int
    error_message: str = ""
    command: tuple[str, ...] = ()
    log_path: str = ""
    run_dir: str = ""
    session_dir: str = ""
    session_reference: str = ""
    skill_version: str = ""
    produced_artifacts: tuple[str, ...] = ()
    runner: str = ""
    # Explicit failure classification; the lease finish path falls back to
    # rule-based classification when these are empty.
    failure_category: str = ""
    failure_detail: str = ""
    # Shard executions return their per-shard output payload here; the lease
    # finish path persists it into node_shards.output_json for reduce fan-in.
    output_json: str = ""


@dataclass(frozen=True)
class LeaseClaimRequest:
    executor_id: str
    global_capacity: int
    workspace_id: str
    job_id: str
    workflow_key: str
    node_key: str
    capability: str
    local_node_limit: int | None
    lease_ttl_seconds: int
    log_path: str
    execution_mode: Literal["full", "until_node"] = "full"
    target_node_key: str | None = None
    allowed_node_keys: tuple[str, ...] = ()
    shard_index: int | None = None


@dataclass(frozen=True)
class ConfigurationFailureRequest:
    workspace_id: str
    job_id: str
    workflow_key: str
    node_key: str
    capability: str
    log_path: str


@dataclass(frozen=True)
class ClaimedExecution:
    lease_id: str
    execution_id: str
    node_run_id: int
    executor_id: str
    workspace_id: str
    job_id: str
    workflow_key: str
    node_key: str
    capability: str
    log_path: str
    shard_index: int | None = None
