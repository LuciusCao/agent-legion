from pydantic import BaseModel, ConfigDict, Field

from server.app.routes.workspace_contracts import WorkspaceRecord


class ExecutorAllocationRequest(BaseModel):
    executor_id: str = Field(min_length=1)
    concurrency_limit: int = Field(ge=1)


class ExecutorAllocationResponse(ExecutorAllocationRequest):
    workspace_id: str


class NodeBindingRequest(BaseModel):
    workflow_key: str = Field(min_length=1)
    node_key: str = Field(min_length=1)
    executor_id: str = Field(min_length=1)


class NodeLimitRequest(BaseModel):
    workflow_key: str = Field(min_length=1)
    node_key: str = Field(min_length=1)
    concurrency_limit: int = Field(ge=1)


class WorkspaceExecutorConfigurationResponse(BaseModel):
    allocations: list[ExecutorAllocationResponse]
    bindings: list[NodeBindingRequest]
    node_limits: list[NodeLimitRequest]
    migration_warnings: list[str]
    agent_capacity: int | None = None


class WorkspaceAgentRouteEntry(BaseModel):
    workflow_key: str
    node_key: str
    node_label: str
    capability: str
    agent_id: str
    agent_skill: str


class WorkspaceAgentRoutesResponse(BaseModel):
    routes: list[WorkspaceAgentRouteEntry]


class WorkspaceSettingsPayload(BaseModel):
    entityType: str
    intakeModes: list[str]
    labelOverrides: dict[str, str]
    workflowKey: str


class WorkspaceConfigurationSettingsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    entityType: str | None = None
    intakeModes: list[str] | None = None
    labelOverrides: dict[str, str] | None = None
    workflowKey: str | None = None


class WorkspaceConfigurationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    description: str | None = None
    settings: WorkspaceConfigurationSettingsRequest
    executor_allocations: list[ExecutorAllocationRequest] = Field(default_factory=list)
    node_bindings: list[NodeBindingRequest] = Field(default_factory=list)
    node_limits: list[NodeLimitRequest] = Field(default_factory=list)
    # Workspace-level Agent concurrency limit; null/absent leaves it unchanged.
    agent_capacity: int | None = Field(default=None, ge=1)


class WorkspaceConfigurationResponse(BaseModel):
    workspace: WorkspaceRecord
    settings: WorkspaceSettingsPayload
    executor_configuration: WorkspaceExecutorConfigurationResponse
    agent_capacity: int | None = None
