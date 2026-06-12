from typing import Any, Literal

from pydantic import BaseModel, Field


class ExecutorDefinitionResponse(BaseModel):
    id: str
    kind: Literal["local", "pi", "openclaw"]
    global_capacity: int = Field(ge=1)
    capabilities: list[str]


class ExecutorCatalogResponse(BaseModel):
    executors: list[ExecutorDefinitionResponse]


class ExecutorAllocationRequest(BaseModel):
    executor_id: str = Field(min_length=1)
    concurrency_limit: int = Field(ge=1)


class ExecutorAllocationResponse(ExecutorAllocationRequest):
    workspace_id: str


class NodeBindingRequest(BaseModel):
    pipeline_key: str = Field(min_length=1)
    node_key: str = Field(min_length=1)
    executor_id: str = Field(min_length=1)


class NodeLimitRequest(BaseModel):
    pipeline_key: str = Field(min_length=1)
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
    pipelineKey: str
    resources: dict[str, Any]
