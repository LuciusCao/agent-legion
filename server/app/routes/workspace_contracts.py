from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class WorkspaceRecord(BaseModel):
    """Workspace row as returned by the workspace queries (decoded configs included)."""

    id: str
    name: str
    description: str
    default_workflow_key: str
    default_entity: str
    resource_config_json: str
    intake_config_json: str
    node_config_json: str
    created_at: str
    updated_at: str
    resource_config: dict[str, Any]
    intake_config: dict[str, Any]
    node_config: dict[str, Any]


class ResourceProviderDefinition(BaseModel):
    key: str
    provider: str
    path: str
    defaultParams: dict[str, str]
    paramKeys: list[str]
    config_schema: dict[str, Any] = Field(default_factory=dict)


class CmsServiceStatus(BaseModel):
    baseUrl: str
    tokenConfigured: bool
    env: str
    healthy: bool | None
    lastCheckedAt: str | None


class ResourceBinding(BaseModel):
    """Per-provider binding stored under resource_config["resources"].

    Extra keys (e.g. provider) are preserved as-is.
    """

    model_config = ConfigDict(extra="allow")

    enabled: bool = False
    config: dict[str, Any] = Field(default_factory=dict)
