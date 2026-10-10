from pydantic import BaseModel

import server.app.routes.workflow_contracts as workflow_contracts


class WorkflowRevisionSummary(BaseModel):
    id: str
    workspace_id: str
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
    # #1143（方案 B）：本次校验的 definition_yaml 的语义身份 hash（不可
    # 解析 → None）。validate_workflow 是草稿卡 YAML 的来源，卡上记录的
    # hash 即取自这里——与编辑器已保存草稿的 hash 核对，消除规范化重排
    # 的「与编辑器不一致」误报。
    definition_hash: str | None = None


class ActiveWorkflowRevisionResponse(BaseModel):
    revision: WorkflowRevisionSummary
    workflow: workflow_contracts.WorkflowDefinitionResponse
    definition_yaml: str


class WorkflowRevisionDetailResponse(BaseModel):
    revision: WorkflowRevisionSummary
    workflow: workflow_contracts.WorkflowDefinitionResponse
    definition_yaml: str
