from typing import Literal

from pydantic import BaseModel

WorkflowChangeType = Literal["added", "removed", "modified"]
WorkflowEdgeChangeType = Literal["added", "removed", "condition_changed", "label_changed"]
WorkflowIntakeChangeType = Literal["mode_changed", "field_added", "field_removed"]
WorkflowRiskLevel = Literal["none", "info", "warning", "breaking"]


class WorkflowDraftCompareRequest(BaseModel):
    definition_yaml: str


class WorkflowDraftCompareError(BaseModel):
    category: str
    message: str
    line: int | None = None
    column: int | None = None
    node_key: str | None = None
    source: str | None = None
    target: str | None = None


class WorkflowRevisionSummaryItem(BaseModel):
    id: str
    version: int
    workflow_key: str
    definition_hash: str


class WorkflowDraftSummaryItem(BaseModel):
    key: str
    label: str
    version: int


class WorkflowNodeChange(BaseModel):
    type: WorkflowChangeType
    node_key: str
    label: str
    fields: list[str]
    risk: WorkflowRiskLevel


class WorkflowEdgeChange(BaseModel):
    type: WorkflowEdgeChangeType
    source: str
    target: str
    before_condition: str | None = None
    after_condition: str | None = None
    risk: WorkflowRiskLevel


class WorkflowIntakeChange(BaseModel):
    type: WorkflowIntakeChangeType
    mode_key: str
    field_key: str | None = None
    risk: WorkflowRiskLevel


class WorkflowRiskFlag(BaseModel):
    code: str
    severity: WorkflowRiskLevel
    message: str


class WorkflowCompareSummary(BaseModel):
    risk_level: WorkflowRiskLevel
    node_changes: list[WorkflowNodeChange]
    edge_changes: list[WorkflowEdgeChange]
    intake_changes: list[WorkflowIntakeChange]
    risk_flags: list[WorkflowRiskFlag]


class WorkflowDraftCompareResponse(BaseModel):
    valid: bool
    base_revision: WorkflowRevisionSummaryItem | None = None
    draft_workflow: WorkflowDraftSummaryItem | None = None
    summary: WorkflowCompareSummary | None = None
    errors: list[WorkflowDraftCompareError] = []
