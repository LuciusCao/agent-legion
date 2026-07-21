from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from server.app.routes.workspace_contracts import (
    CmsServiceStatus,
    ResourceProviderDefinition,
    WorkspaceRecord,
)


class JobBatchRequest(BaseModel):
    workflow_key: str = "question_comprehension_info"
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
    default_entity: str = "question"
    cms_config: dict[str, Any] = Field(default_factory=dict)
    resource_config: dict[str, Any] = Field(default_factory=dict)
    intake_config: dict[str, Any] = Field(default_factory=dict)


class WorkspaceUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    description: str | None = None
    default_workflow_key: str | None = None
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
    workflowKey: str | None = None
    resources: dict[str, Any] | None = None


class WorkspaceSettingsTestResponse(BaseModel):
    ok: bool
    message: str


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


class ExecutorRuntimeStatus(BaseModel):
    executor_id: str
    kind: str
    global_capacity: int
    workspace_limit: int
    running: int
    available: int
    binding_count: int


class ExecutorStatusSummary(BaseModel):
    executors: list[ExecutorRuntimeStatus]


class WorkspaceStatsResponse(BaseModel):
    workspace_id: str
    name: str
    workflow_key: str
    workflow_label: str
    job_stats: dict[str, int]
    executor_status: ExecutorStatusSummary
    latest_run: dict[str, Any] | None


class DeleteWorkspaceResponse(BaseModel):
    deleted: str


class ResourceProvidersResponse(BaseModel):
    providers: list[ResourceProviderDefinition]


class GlobalServicesResponse(BaseModel):
    cms: CmsServiceStatus
