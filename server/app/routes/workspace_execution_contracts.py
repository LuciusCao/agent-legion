from pydantic import BaseModel, ConfigDict, Field

from server.app.routes.workspace_contracts import WorkspaceRecord


class NodeLimitRequest(BaseModel):
    # #211 M3: the workflow_key member is gone — the workspace id in the path
    # identifies the workflow (an old client still sending it is ignored).
    node_key: str = Field(min_length=1)
    concurrency_limit: int = Field(ge=1)


class NodeLimitEntry(NodeLimitRequest):
    """Response twin of NodeLimitRequest (#269 split, kept as a named shape)."""


class WorkspaceExecutionConfigurationResponse(BaseModel):
    """Workspace execution configuration (P-0.5: node limits + Agent capacity).

    Allocations and bindings retired with the executor concept (schema v47);
    issue #198 renamed the type from the pre-retirement
    ``WorkspaceExecutorConfigurationResponse`` wording.
    """

    node_limits: list[NodeLimitEntry]
    migration_warnings: list[str]
    agent_capacity: int | None = None


class WorkspaceAgentRouteEntry(BaseModel):
    node_key: str
    node_label: str
    capability: str
    agent_id: str
    agent_skill: str


class WorkspaceAgentRoutesResponse(BaseModel):
    routes: list[WorkspaceAgentRouteEntry]


class WorkspaceSettingsPayload(BaseModel):
    entityType: str
    previewHidden: list[str] = Field(default_factory=list)


class WorkspaceConfigurationSettingsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    entityType: str | None = None
    # Workspace 级产物预览隐藏列表（job 详情左栏）。PUT 全量保存时缺省
    # 表示「未改」——沿用已存配置，避免旧客户端 PUT 抹掉勾选。
    previewHidden: list[str] | None = None


class WorkspaceConfigurationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    description: str | None = None
    settings: WorkspaceConfigurationSettingsRequest
    node_limits: list[NodeLimitRequest] = Field(default_factory=list)
    # Workspace-level Agent concurrency limit; null/absent leaves it unchanged.
    agent_capacity: int | None = Field(default=None, ge=1)


class WorkspaceConfigurationResponse(BaseModel):
    workspace: WorkspaceRecord
    settings: WorkspaceSettingsPayload
    execution_configuration: WorkspaceExecutionConfigurationResponse
    agent_capacity: int | None = None
