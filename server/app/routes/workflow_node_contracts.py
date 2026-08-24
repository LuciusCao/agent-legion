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
    # default_factory (not a plain default) keeps the field out of the OpenAPI
    # ``default`` keyword so generated TS treats it as optional — same idiom
    # as ``execution`` below; response payloads always carry both fields.
    node_type: str = Field(default_factory=lambda: "node")
    accepted_item_types: list[str] = Field(default_factory=lambda: ["material", "ref"])
    after: list[str]
    inputs: list[str]
    outputs: list[str]
    terminal: WorkflowTerminalResponse | None = None
    execution: WorkflowNodeExecutionResponse = Field(default_factory=WorkflowNodeExecutionResponse)
