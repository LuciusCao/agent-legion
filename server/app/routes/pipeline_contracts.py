from typing import Any

from pydantic import BaseModel


class PipelineSummaryResponse(BaseModel):
    key: str
    label: str


class PipelineIntakeModeResponse(BaseModel):
    key: str
    label: str
    input_field: str
    resource: str


class PipelineIntakeResponse(BaseModel):
    modes: list[PipelineIntakeModeResponse]


class PipelineNodeResponse(BaseModel):
    key: str
    label: str
    capability: str
    after: list[str]
    inputs: list[str]
    outputs: list[str]


class PipelineDefinitionResponse(PipelineSummaryResponse):
    intake: PipelineIntakeResponse
    nodes: list[PipelineNodeResponse]


class PipelineResponse(BaseModel):
    pipeline: PipelineDefinitionResponse


class PipelinesListResponse(BaseModel):
    pipelines: list[PipelineSummaryResponse]


def pipeline_response(value: dict[str, Any]) -> PipelineResponse:
    return PipelineResponse(pipeline=PipelineDefinitionResponse.model_validate(value))


def pipelines_list_response(values: list[dict[str, Any]]) -> PipelinesListResponse:
    return PipelinesListResponse(
        pipelines=[PipelineSummaryResponse.model_validate(value) for value in values]
    )
