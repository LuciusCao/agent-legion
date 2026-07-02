from typing import Any

from pydantic import BaseModel


class WorkflowSummaryResponse(BaseModel):
    key: str
    label: str


class WorkflowIntakeModeResponse(BaseModel):
    key: str
    label: str
    input_field: str
    resource: str


class WorkflowIntakeResponse(BaseModel):
    modes: list[WorkflowIntakeModeResponse]


class WorkflowNodeResponse(BaseModel):
    key: str
    label: str
    capability: str
    after: list[str]
    inputs: list[str]
    outputs: list[str]


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


class WorkflowsListResponse(BaseModel):
    workflows: list[WorkflowSummaryResponse]
