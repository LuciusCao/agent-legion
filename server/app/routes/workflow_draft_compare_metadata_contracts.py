from typing import Literal

from pydantic import BaseModel

from server.app.routes.workflow_risk_level import WorkflowRiskLevel


class WorkflowMetadataChange(BaseModel):
    type: Literal["modified"]
    field: str
    before_value: str | None = None
    after_value: str | None = None
    risk: WorkflowRiskLevel
