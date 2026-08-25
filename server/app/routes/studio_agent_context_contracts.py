"""Response contracts for the studio-agent session context endpoint (v45)."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class StudioContextNode(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str
    capability: str


class StudioContextEdge(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: str
    target: str


class StudioContextWorkflow(BaseModel):
    """Structural summary of the workspace's active workflow revision."""

    model_config = ConfigDict(extra="forbid")

    workflow_key: str
    version: int
    nodes: list[StudioContextNode]
    edges: list[StudioContextEdge]


class StudioChatContextResponse(BaseModel):
    """What the get_studio_context MCP tool returns: the session's bound
    workspace, the human's live Studio node selection, the canvas' unpublished
    workflow draft (None until the frontend pushes it), and the active
    workflow's structure. ``workflow`` is None when nothing is published yet."""

    model_config = ConfigDict(extra="forbid")

    workspace_id: str
    selected_node_key: str | None
    draft_yaml: str | None
    workflow: StudioContextWorkflow | None
