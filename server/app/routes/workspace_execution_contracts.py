from pydantic import BaseModel, ConfigDict, Field

from server.app.routes.workspace_contracts import WorkspaceRecord

# #211 Phase 2 second batch deprecation wording (shared by the settings
# blob's read/write faces).
_DEPRECATED_KEY = (
    "Deprecated: equals the workspace id since schema v62; removal is tracked in #211."
)


class NodeLimitRequest(BaseModel):
    workflow_key: str = Field(min_length=1)
    node_key: str = Field(min_length=1)
    concurrency_limit: int = Field(ge=1)


class WorkspaceExecutionConfigurationResponse(BaseModel):
    """Workspace execution configuration (P-0.5: node limits + Agent capacity).

    Allocations and bindings retired with the executor concept (schema v47);
    issue #198 renamed the type from the pre-retirement
    ``WorkspaceExecutorConfigurationResponse`` wording.
    """

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
    # Response-side blob: still emitted (Phase 3/4 removes the column) but
    # optional — callers round-trip a value they cannot change.
    entityType: str
    workflowKey: str | None = Field(default=None, description=_DEPRECATED_KEY, deprecated=True)
    previewHidden: list[str] = Field(default_factory=list)


class WorkspaceConfigurationSettingsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    entityType: str | None = None
    # PUT-side: absent = keep stored (previewHidden's compatibility pattern);
    # a matching old-snapshot value is a no-op, a different value still hits
    # the immutable-key 400.
    workflowKey: str | None = Field(default=None, description=_DEPRECATED_KEY, deprecated=True)
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
