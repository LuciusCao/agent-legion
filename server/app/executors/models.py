from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

ExecutionStatus = Literal["completed", "failed", "cancelled"]

# Single implicit code pool (P-0.5): every non-Agent-routed node claims and
# runs under this executor id. The executor_leases.executor_id column keeps
# its historical values; new rows always carry this constant.
CODE_EXECUTOR_ID = "code"


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
    # Effective node config resolved at dispatch (spec D15); empty for
    # executors whose capability declares no config_schema.
    node_config: Mapping[str, Any] = field(default_factory=dict)
    # Custom node code text resolved at dispatch (EXEC-CODE-002); None means
    # the builtin repo-tracked implementation. The code text rides the context
    # (instead of a DB reference the child would re-resolve) so the isolated
    # child process needs no DB access for code loading — the parent already
    # does the per-dispatch DB reads for secrets, so this adds no extra round
    # trip. This deviates from design §5's "DB read inside the child" for that
    # simplicity reason.
    node_code: str | None = None


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
    # executor_id is always CODE_EXECUTOR_ID (single code pool, P-0.5);
    # global_capacity is filled server-side from the instance settings
    # code_capacity, never chosen by the caller.
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
    # Non-secret resolved node config at dispatch (CONFIG-RUNTIME-MUTABLE-001
    # audit); persisted onto the node_runs row created by the claim.
    config_snapshot_json: str = ""


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
