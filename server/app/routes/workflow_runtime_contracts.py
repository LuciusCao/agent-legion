"""Contracts for the workflow runtime channel endpoints (M47)."""

from __future__ import annotations

from pydantic import BaseModel

from server.app.routes.workflow_revisions_contracts import WorkflowRevisionSummary


class WorkflowRuntimeSaveRequest(BaseModel):
    """Full definition YAML; only the runtime slice may differ from active."""

    definition_yaml: str


class RuntimeConfigVersionSummary(BaseModel):
    version: int
    revision_id: str
    changed_keys: list[str]
    created_by: str
    created_at: str


class WorkflowRuntimeSaveResponse(BaseModel):
    revision: WorkflowRevisionSummary
    # None on a no-change save (the runtime slice already matched; nothing
    # was appended).
    runtime_version: RuntimeConfigVersionSummary | None = None


class WorkflowRuntimeVersionsResponse(BaseModel):
    versions: list[RuntimeConfigVersionSummary]


class WorkflowRuntimeRollbackRequest(BaseModel):
    version: int
