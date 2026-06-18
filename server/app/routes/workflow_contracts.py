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


class WorkflowDefinitionResponse(WorkflowSummaryResponse):
    intake: WorkflowIntakeResponse
    nodes: list[WorkflowNodeResponse]


class WorkflowResponse(BaseModel):
    workflow: WorkflowDefinitionResponse


class WorkflowsListResponse(BaseModel):
    workflows: list[WorkflowSummaryResponse]


def workflow_response(value: dict[str, Any]) -> WorkflowResponse:
    return WorkflowResponse(workflow=WorkflowDefinitionResponse.model_validate(value))


def workflows_list_response(values: list[dict[str, Any]]) -> WorkflowsListResponse:
    return WorkflowsListResponse(
        workflows=[WorkflowSummaryResponse.model_validate(value) for value in values]
    )
