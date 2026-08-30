from typing import Literal

from pydantic import BaseModel

from server.app.routes.workflow_draft_compare_metadata_contracts import (
    WorkflowMetadataChange,
)
from server.app.routes.workflow_risk_level import WorkflowRiskLevel

WorkflowChangeType = Literal["added", "removed", "modified"]
WorkflowEdgeChangeType = Literal["added", "removed", "condition_changed", "label_changed"]
WorkflowIntakeChangeType = Literal["mode_changed", "field_added", "field_removed"]


class WorkflowDraftCompareRequest(BaseModel):
    definition_yaml: str
    # Studio empty mode (never-published workflow) sets this to preview the
    # draft against an empty baseline instead of failing with a revision error.
    allow_missing_baseline: bool = False


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
    # 'start' marks the entry node (explicit or loader-injected synthetic) so
    # the canvas can synthesize its inspector details from a draft that does
    # not declare it; 'approval' marks a human decision gate (EXEC-APPROVAL-001).
    node_type: Literal["start", "node", "approval"] = "node"
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
    metadata_changes: list[WorkflowMetadataChange] = []
    risk_flags: list[WorkflowRiskFlag]


class WorkflowDraftCompareResponse(BaseModel):
    valid: bool
    creates_revision: bool = False
    base_revision: WorkflowRevisionSummaryItem | None = None
    draft_workflow: WorkflowDraftSummaryItem | None = None
    summary: WorkflowCompareSummary | None = None
    errors: list[WorkflowDraftCompareError] = []
