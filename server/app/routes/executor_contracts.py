from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class ExecutorCapabilityResponse(BaseModel):
    name: str
    handler: str | None = None
    skill: str | None = None
    tools: list[str] = Field(default_factory=list)


class ExecutorDefinitionResponse(BaseModel):
    id: str
    kind: Literal["local", "pi", "openclaw"]
    global_capacity: int = Field(ge=1)
    capabilities: list[str]
    capability_details: list[ExecutorCapabilityResponse] = Field(default_factory=list)


class ExecutorCatalogResponse(BaseModel):
    executors: list[ExecutorDefinitionResponse]


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


class WorkspaceSettingsPayload(BaseModel):
    entityType: str
    intakeModes: list[str]
    labelOverrides: dict[str, str]
    workflowKey: str
    resources: dict[str, Any]


class WorkspaceConfigurationSettingsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    entityType: str | None = None
    intakeModes: list[str] | None = None
    labelOverrides: dict[str, str] | None = None
    workflowKey: str | None = None
    resources: dict[str, Any] | None = None


class WorkspaceConfigurationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    description: str | None = None
    settings: WorkspaceConfigurationSettingsRequest
    executor_allocations: list[ExecutorAllocationRequest] = Field(default_factory=list)
    node_bindings: list[NodeBindingRequest] = Field(default_factory=list)
    node_limits: list[NodeLimitRequest] = Field(default_factory=list)


class WorkspaceConfigurationResponse(BaseModel):
    workspace: dict[str, Any]
    settings: WorkspaceSettingsPayload
    executor_configuration: WorkspaceExecutorConfigurationResponse
