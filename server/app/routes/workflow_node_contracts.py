from pydantic import BaseModel, Field


class WorkflowTerminalResponse(BaseModel):
    outcome: str


class WorkflowNodeExecutionResponse(BaseModel):
    provider: str = ""
    model: str = ""
    thinking: str = ""
    prompt: str = ""


class WorkflowNodeResponse(BaseModel):
    key: str
    label: str
    capability: str
    max_concurrency: int | None = None
    after: list[str]
    inputs: list[str]
    outputs: list[str]
    terminal: WorkflowTerminalResponse | None = None
    execution: WorkflowNodeExecutionResponse = Field(default_factory=WorkflowNodeExecutionResponse)
