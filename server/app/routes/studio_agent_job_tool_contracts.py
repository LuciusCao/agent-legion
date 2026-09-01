"""API contracts for the studio-agent job observation tools (#329).

All read-only. Payloads are diagnosis-shaped trims of the human-facing job
views: no input/config snapshots, no local filesystem paths (logs are fetched
through the logs endpoint, which sanitizes).
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class StudioAgentJobSummaryNode(BaseModel):
    model_config = ConfigDict(extra="forbid")

    node_key: str
    label: str
    status: str
    error_message: str


class StudioAgentJobView(BaseModel):
    """Per-job summary used by list/detail/compare/context responses."""

    model_config = ConfigDict(extra="forbid")

    id: str
    title: str
    status: str
    outcome: str
    created_at: datetime | None
    updated_at: datetime | None
    error_summary: str
    active_node_key: str | None
    completed_nodes: int
    total_nodes: int
    is_workflow_outdated: bool
    node_summaries: list[StudioAgentJobSummaryNode]


class StudioAgentJobListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    jobs: list[StudioAgentJobView]
    returned: int
    limit: int


class StudioAgentJobNode(BaseModel):
    model_config = ConfigDict(extra="forbid")

    node_key: str
    label: str
    capability: str
    status: str
    error_message: str
    inputs: list[str]
    outputs: list[str]
    executor_kind: str | None
    agent_id: str | None


class StudioAgentJobRun(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: int
    node_key: str
    status: str
    error_message: str
    started_at: datetime | None
    finished_at: datetime | None
    has_log: bool


class StudioAgentSuggestedAction(BaseModel):
    """A suggestion payload the UI turns into a human-confirmation card; the
    mutation itself always runs through the host session (STUDIO-AGENT-001)."""

    model_config = ConfigDict(extra="forbid")

    action: str
    job_id: str
    node_key: str
    label: str
    requires_confirmation: bool


class StudioAgentJobDetail(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job: StudioAgentJobView
    nodes: list[StudioAgentJobNode]
    runs: list[StudioAgentJobRun]
    artifacts: list[str]
    suggested_actions: list[StudioAgentSuggestedAction]


class StudioAgentJobLogsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job_id: str
    run_id: int
    node_key: str
    status: str
    error_message: str
    log: str
    truncated: bool


class StudioAgentArtifactResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    content: str
    truncated: bool


class StudioAgentJobCompareNode(BaseModel):
    model_config = ConfigDict(extra="forbid")

    node_key: str
    status_a: str
    status_b: str
    error_a: str
    error_b: str
    changed: bool


class StudioAgentJobCompareSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    nodes_changed: int
    newly_failed: list[str]
    recovered: list[str]


class StudioAgentJobCompareResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job_a: StudioAgentJobView
    job_b: StudioAgentJobView
    nodes: list[StudioAgentJobCompareNode]
    summary: StudioAgentJobCompareSummary


class StudioAgentRecentFailure(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job_id: str
    node_key: str
    failure_category: str
    error_message: str
    finished_at: datetime | None


class StudioAgentJobContextResponse(BaseModel):
    """The session-bound context payload: which workspace/job the diagnosis
    conversation operates on, the focus node (explicit or the job's active
    one), the full job detail, and other jobs' recent failures on the focus
    node (the flaky-or-new signal)."""

    model_config = ConfigDict(extra="forbid")

    session_id: str
    workspace_id: str
    focus_node_key: str | None
    job: StudioAgentJobDetail
    recent_failures: list[StudioAgentRecentFailure]
