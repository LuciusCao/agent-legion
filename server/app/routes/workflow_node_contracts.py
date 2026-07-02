from pydantic import BaseModel


class WorkflowTerminalResponse(BaseModel):
    outcome: str


class WorkflowNodeResponse(BaseModel):
    key: str
    label: str
    capability: str
    after: list[str]
    inputs: list[str]
    outputs: list[str]
    terminal: WorkflowTerminalResponse | None = None
