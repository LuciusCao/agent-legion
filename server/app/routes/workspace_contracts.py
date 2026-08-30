from typing import Any

from pydantic import BaseModel, Field


class WorkspaceRecord(BaseModel):
    """Workspace row as returned by the workspace queries (decoded configs included)."""

    id: str
    name: str
    description: str
    default_workflow_key: str = Field(
        description=(
            "Deprecated: read id instead. Since schema v62 the two are always "
            "equal; removal is tracked in #211."
        ),
        deprecated=True,
    )
    default_entity: str
    resource_config_json: str
    node_config_json: str
    created_at: str
    updated_at: str
    resource_config: dict[str, Any]
    node_config: dict[str, Any]
