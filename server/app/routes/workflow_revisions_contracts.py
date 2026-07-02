from pydantic import BaseModel

import server.app.routes.workflow_contracts as workflow_contracts


class WorkflowRevisionSummary(BaseModel):
    id: str
    workspace_id: str
    workflow_key: str
    version: int
    status: str
    definition_hash: str
    created_at: str
    published_at: str | None = None


class WorkflowRevisionsResponse(BaseModel):
    revisions: list[WorkflowRevisionSummary]


class WorkflowDraftRequest(BaseModel):
    definition_yaml: str


class WorkflowDraftValidationResponse(BaseModel):
    valid: bool
    errors: list[str]


class ActiveWorkflowRevisionResponse(BaseModel):
    revision: WorkflowRevisionSummary
    workflow: workflow_contracts.WorkflowDefinitionResponse
    definition_yaml: str
