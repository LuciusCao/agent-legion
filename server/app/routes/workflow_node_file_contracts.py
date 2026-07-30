from pydantic import BaseModel, Field


class WorkflowNodeCapabilityReference(BaseModel):
    executor_id: str
    capability: str


class WorkflowNodeFileResponse(BaseModel):
    path: str
    content: str
    capabilities: list[WorkflowNodeCapabilityReference] = Field(default_factory=list)


class WorkflowNodeFileUpdateRequest(BaseModel):
    content: str


class WorkflowNodeFileUpdateResponse(BaseModel):
    path: str
    capabilities: list[WorkflowNodeCapabilityReference] = Field(default_factory=list)
