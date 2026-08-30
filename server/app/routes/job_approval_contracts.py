"""Pydantic contracts for the approval-gate decision API (EXEC-APPROVAL-001)."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

ApprovalVerdict = Literal["approved", "rework", "rejected"]


class ApprovalDecisionCreateRequest(BaseModel):
    verdict: ApprovalVerdict
    #: Mandatory for rework (the reviewer's 修改意见, written as the gate's
    #: feedback artifact for the regenerating skill); optional otherwise.
    note: str = Field(default="", max_length=20_000)
    #: Rework only: upstream node to reset. Falls back to the gate's
    #: config.rework_target; the service validates ancestry.
    rework_target: str = ""


class ApprovalDecisionResponse(BaseModel):
    id: str
    job_id: str
    node_key: str
    verdict: ApprovalVerdict
    note: str
    rework_target: str
    decided_by: str
    created_at: datetime | None = None


class ApprovalDecisionListResponse(BaseModel):
    decisions: list[ApprovalDecisionResponse]
