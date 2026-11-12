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
    workflow's structure. ``workflow`` is None when nothing is published yet.

    #1226 八轮复审 P1：会话镜像的 draft_yaml 与代次分属两条通道，任何此处
    另读的代次都可能与 YAML 错配——因此响应不携带代次；save_workflow_draft
    的 CAS base 一律从 get_workflow_draft 的原子状态读取。"""

    model_config = ConfigDict(extra="forbid")

    workspace_id: str
    selected_node_key: str | None
    draft_yaml: str | None
    workflow: StudioContextWorkflow | None
