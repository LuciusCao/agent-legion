from typing import Literal

from pydantic import BaseModel, Field

from server.app.routes.job_execution_control_contracts import ExecutionControlSummaryResponse


class JobNodeSummaryResponse(BaseModel):
    node_key: str
    label: str
    status: str
    error_message: str


class JobSummaryResponse(BaseModel):
    id: str
    workspace_id: str
    workflow_key: str = Field(
        description=(
            "Deprecated: read workspace_id instead. Since schema v62 the two "
            "are always equal; removal is tracked in #211."
        ),
        deprecated=True,
    )
    source_type: str
    source_id: str
    batch_id: str
    title: str
    status: str
    storage_dir: str
    error_message: str
    created_at: str
    updated_at: str
    workflow_revision_id: str = ""
    workflow_version: int | None = None
    workflow_definition_hash: str = ""
    outcome: str = ""
    current_workflow_revision_id: str = ""
    current_workflow_revision_version: int | None = None
    is_workflow_outdated: bool = False
    node_summaries: list[JobNodeSummaryResponse] = Field(default_factory=list)
    completed_nodes: int = 0
    total_nodes: int = 0
    active_node_key: str | None = None
    error_summary: str = ""
    packed: int = 0
    execution_control: ExecutionControlSummaryResponse = Field(
        default_factory=ExecutionControlSummaryResponse
    )


class JobsResponse(BaseModel):
    jobs: list[JobSummaryResponse]


class JobsSnapshotResponse(BaseModel):
    workspace_id: str
    revision: int
    stats: dict[str, int]
    jobs: list[JobSummaryResponse]
    next_cursor: str | None = None


class JobNodeResponse(BaseModel):
    id: int
    job_id: str
    node_key: str
    status: str
    stale_reason: str
    error_message: str
    started_at: str | None = None
    finished_at: str | None = None
    created_at: str
    label: str
    capability: str
    after: list[str]
    inputs: list[str]
    outputs: list[str]
    executor_id: str | None = None
    executor_kind: Literal["code", "pi", "openclaw"] | None = None
    agent_id: str | None = None
    worker_id: str | None = None


class NodeRunResponse(BaseModel):
    id: int
    job_id: str
    node_key: str
    status: str
    started_at: str
    finished_at: str | None = None
    command_json: str
    exit_code: int | None = None
    log_path: str
    error_message: str
    run_dir: str
    session_dir: str
    runner: str = ""


class LogEventResponse(BaseModel):
    type: str
    title: str
    detail: str
    truncated: bool


class JobLogResponse(BaseModel):
    run_id: int
    log: str
    truncated: bool
    structured: list[LogEventResponse] | None = None
    raw_url: str | None = None


class JobDetailResponse(BaseModel):
    job: JobSummaryResponse
    nodes: list[JobNodeResponse]
    runs: list[NodeRunResponse]
    artifacts: list[str]
