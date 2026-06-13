from typing import Any

from pydantic import BaseModel, ConfigDict, Field, RootModel


class JobBatchRequest(BaseModel):
    pipeline_key: str = "reading_analysis"
    entity: str | None = None
    source_kind: str
    question_ids: list[str] = Field(default_factory=list)
    knowledge_codes: list[str] = Field(default_factory=list)


class JobBatchResponse(BaseModel):
    batch: dict[str, Any]
    created_count: int
    jobs: list[dict[str, Any]]


class JobsResponse(BaseModel):
    jobs: list[dict[str, Any]]


class PipelineResponse(BaseModel):
    pipeline: dict[str, Any]


class PipelinesListResponse(BaseModel):
    pipelines: list[dict[str, Any]]


class WorkspaceCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    default_pipeline_key: str = "reading_analysis"
    default_entity: str = "question"
    cms_config: dict[str, Any] = Field(default_factory=dict)
    resource_config: dict[str, Any] = Field(default_factory=dict)
    intake_config: dict[str, Any] = Field(default_factory=dict)


class WorkspaceUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    description: str | None = None
    default_pipeline_key: str | None = None
    default_entity: str | None = None
    cms_config: dict[str, Any] | None = None
    resource_config: dict[str, Any] | None = None
    intake_config: dict[str, Any] | None = None


class WorkspaceSettingsResponse(BaseModel):
    settings: dict[str, Any]


class WorkspaceSettingsSectionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cmsUrl: str | None = None
    cmsToken: str | None = None
    entityType: str | None = None
    intakeModes: list[str] | None = None
    labelOverrides: dict[str, str] | None = None
    pipelineKey: str | None = None
    resources: dict[str, Any] | None = None


class WorkspaceSettingsTestResponse(BaseModel):
    ok: bool
    message: str


class WorkspaceResponse(BaseModel):
    workspace: dict[str, Any]


class WorkspacesResponse(BaseModel):
    workspaces: list[dict[str, Any]]


class WorkspaceAgentsResponse(BaseModel):
    agents: list[dict[str, Any]]


class WorkspaceAgentAssignmentResponse(BaseModel):
    agent_id: str
    workspace_id: str
    concurrency_limit: int


class WorkspaceAgentListResponse(RootModel[list[WorkspaceAgentAssignmentResponse]]):
    pass


class DeleteJobResponse(BaseModel):
    deleted: str


class JobDetailResponse(BaseModel):
    job: dict[str, Any]
    nodes: list[dict[str, Any]]
    runs: list[dict[str, Any]]
    artifacts: list[str]


class ArtifactResponse(BaseModel):
    name: str
    content: str


class RerunNodeResponse(BaseModel):
    job_id: str
    node_key: str
    stale_nodes: list[str]


class BatchJobRequest(BaseModel):
    job_ids: list[str] = Field(default_factory=list)


class BatchJobResponse(BaseModel):
    results: list[dict[str, Any]]


class WorkspaceRunsResponse(BaseModel):
    runs: list[dict[str, Any]]


class WorkspaceDagResponse(BaseModel):
    pipeline: dict[str, Any]
    nodes: list[dict[str, Any]]


class WorkspaceAgentConfig(BaseModel):
    agent_id: str
    concurrency_limit: int


class WorkspaceAgentStatus(BaseModel):
    id: str
    name: str
    busy: bool


class WorkspaceStatsResponse(BaseModel):
    workspace_id: str
    name: str
    pipeline_key: str
    pipeline_label: str
    job_stats: dict[str, int]
    agent_status: dict[str, Any]
    latest_run: dict[str, Any] | None


class DeleteWorkspaceResponse(BaseModel):
    deleted: str


class ResourceProvidersResponse(BaseModel):
    providers: list[dict[str, Any]]


class GlobalServicesResponse(BaseModel):
    cms: dict[str, Any]
