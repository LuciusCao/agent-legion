from typing import Any

from pydantic import BaseModel


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
