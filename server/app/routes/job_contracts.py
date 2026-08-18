from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from server.app.routes.workspace_contracts import WorkspaceRecord


class JobBatchRequest(BaseModel):
    # Intake input fields are workflow-definition-driven (mode.input_field),
    # so extra fields pass through to the intake service verbatim.
    model_config = ConfigDict(extra="allow")

    # No platform default workflow: callers must choose explicitly.
    workflow_key: str
    entity: str | None = None
    source_kind: str
    question_ids: list[str] = Field(default_factory=list)
    knowledge_codes: list[str] = Field(default_factory=list)
    async_processing: bool = False


class JobBatchResponse(BaseModel):
    batch: dict[str, Any]
    created_count: int
    jobs: list[dict[str, Any]]


class WorkspaceCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    default_workflow_key: str
    # demo (default): bind the catalog definition and seed the active revision
    # (including factory Agent templates for the built-in demo workflow).
    # blank: only record the default_workflow_key slot; Studio starts from an
    # empty draft and the first publish creates revision v1.
    workflow_mode: Literal["demo", "blank"] = "demo"
    default_entity: str = "question"
    resource_config: dict[str, Any] = Field(default_factory=dict)
    intake_config: dict[str, Any] = Field(default_factory=dict)


class WorkspaceUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    description: str | None = None
    default_workflow_key: str | None = None
    default_entity: str | None = None
    resource_config: dict[str, Any] | None = None
    intake_config: dict[str, Any] | None = None


class WorkspaceSettingsResponse(BaseModel):
    settings: dict[str, Any]


class WorkspaceSettingsSectionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    entityType: str | None = None
    intakeModes: list[str] | None = None
    labelOverrides: dict[str, str] | None = None
    workflowKey: str | None = None
    nodeConfig: dict[str, dict[str, Any]] | None = None
    agentDefaults: dict[str, str] | None = None


class WorkspaceResponse(BaseModel):
    workspace: WorkspaceRecord


class WorkspacesResponse(BaseModel):
    workspaces: list[WorkspaceRecord]


class DeleteJobResponse(BaseModel):
    deleted: str


class ArtifactResponse(BaseModel):
    name: str
    content: str


class WorkspaceRunsResponse(BaseModel):
    runs: list[dict[str, Any]]


class WorkspaceDagResponse(BaseModel):
    workflow: dict[str, Any]
    nodes: list[dict[str, Any]]


class CodePoolStatus(BaseModel):
    """The single implicit code pool (P-0.5): instance-wide capacity, this
    workspace's running count, and the globally available slots."""

    capacity: int
    running: int
    available: int


class WorkspaceStatsResponse(BaseModel):
    workspace_id: str
    name: str
    workflow_key: str
    workflow_label: str
    job_stats: dict[str, int]
    code_pool: CodePoolStatus
    latest_run: dict[str, Any] | None


class DeleteWorkspaceResponse(BaseModel):
    deleted: str
