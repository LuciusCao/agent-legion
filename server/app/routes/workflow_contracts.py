from typing import Any

from pydantic import BaseModel

from server.app.routes.workflow_node_contracts import WorkflowNodeResponse


class WorkflowSummaryResponse(BaseModel):
    key: str
    label: str


class WorkflowIntakeModeResponse(BaseModel):
    key: str
    label: str
    input_field: str


class WorkflowIntakeResponse(BaseModel):
    modes: list[WorkflowIntakeModeResponse]


class WorkflowConditionResponse(BaseModel):
    artifact: str
    path: str
    equals: Any


class WorkflowEdgeResponse(BaseModel):
    source: str
    target: str
    condition: WorkflowConditionResponse | None = None


class WorkflowDefinitionResponse(WorkflowSummaryResponse):
    intake: WorkflowIntakeResponse
    nodes: list[WorkflowNodeResponse]
    edges: list[WorkflowEdgeResponse]


class WorkflowResponse(BaseModel):
    workflow: WorkflowDefinitionResponse
