from typing import Literal

from pydantic import BaseModel, Field


class ExecutionControlSummaryResponse(BaseModel):
    mode: Literal["full", "until_node"] = "full"
    target_node_key: str | None = None
    paused: bool = False
    pause_reason: str = ""


class JobNodeSummaryResponse(BaseModel):
    node_key: str
    label: str
    status: str
    error_message: str


class JobSummaryResponse(BaseModel):
    id: str
    workspace_id: str
    pipeline_key: str
    source_type: str
    source_id: str
    batch_id: str
    title: str
    status: str
    storage_dir: str
    error_message: str
    created_at: str
    updated_at: str
    node_summaries: list[JobNodeSummaryResponse] = Field(default_factory=list)
    completed_nodes: int = 0
    total_nodes: int = 0
    active_node_key: str | None = None
    error_summary: str = ""
    execution_control: ExecutionControlSummaryResponse = Field(
        default_factory=ExecutionControlSummaryResponse
    )


class JobsResponse(BaseModel):
    jobs: list[JobSummaryResponse]


class JobNodeResponse(BaseModel):
    id: int
    job_id: str
    node_key: str
    status: str
    stale_reason: str
    error_message: str
    started_at: str | None = None
    finished_at: str | None = None
    label: str
    capability: str
    after: list[str]
    inputs: list[str]
    outputs: list[str]


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


class JobDetailResponse(BaseModel):
    job: JobSummaryResponse
    nodes: list[JobNodeResponse]
    runs: list[NodeRunResponse]
    artifacts: list[str]
