from collections.abc import Mapping
from dataclasses import dataclass
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
    pipeline_key: str
    node_key: str
    capability: str
    workspace: Mapping[str, object]
    job: Mapping[str, object]
    job_dir: Path
    log_path: Path
    inputs: tuple[str, ...]
    expected_outputs: tuple[str, ...]


@dataclass(frozen=True)
class ExecutionResult:
    status: ExecutionStatus
    exit_code: int
    error_message: str = ""
    command: tuple[str, ...] = ()
    log_path: str = ""
    session_reference: str = ""
    produced_artifacts: tuple[str, ...] = ()


@dataclass(frozen=True)
class LeaseClaimRequest:
    executor_id: str
    global_capacity: int
    workspace_id: str
    job_id: str
    pipeline_key: str
    node_key: str
    capability: str
    local_node_limit: int | None
    lease_ttl_seconds: int
    log_path: str


@dataclass(frozen=True)
class ConfigurationFailureRequest:
    workspace_id: str
    job_id: str
    pipeline_key: str
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
    pipeline_key: str
    node_key: str
    capability: str
    log_path: str
