"""Approval-gate decision routes (EXEC-APPROVAL-001).

Decisions are accepted from human sessions only: every mutating route
rejects studio_agent scoped tokens, so no in-app agent can ever approve on
a person's behalf. Workspace access is enforced by the surrounding job
route group (editor for POST, viewer for GET); the actor lands on the
insert-only decision row in the uniform ``user:{id}`` format.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends

from server.app.auth.dependencies import reject_studio_agent_scope, require_user
from server.app.routes.job_approval_contracts import (
    ApprovalDecisionCreateRequest,
    ApprovalDecisionListResponse,
    ApprovalDecisionResponse,
)
from server.app.routes.job_http import (
    raise_job_http_error,
    raise_job_operation_error,
    require_workflows_enabled,
)
from server.app.services.approval_decisions import ApprovalDecisionService
from server.app.services.job_errors import JobServiceError
from server.app.services.job_operation_error import JobOperationError
from server.app.settings import Settings


def create_job_approvals_router(
    approvals: ApprovalDecisionService, settings: Settings
) -> APIRouter:
    router = APIRouter()

    @router.post(
        "/workspaces/{workspace_id}/jobs/{job_id}/nodes/{node_key}/approval",
        response_model=ApprovalDecisionResponse,
        dependencies=[Depends(reject_studio_agent_scope)],
    )
    def decide_approval(
        workspace_id: str,
        job_id: str,
        node_key: str,
        payload: ApprovalDecisionCreateRequest,
        user: Annotated[dict[str, Any], Depends(require_user)],
    ) -> ApprovalDecisionResponse:
        require_workflows_enabled(settings)
        try:
            decision = approvals.decide(
                workspace_id,
                job_id,
                node_key,
                verdict=payload.verdict,
                note=payload.note,
                rework_target=payload.rework_target,
                decided_by=f"user:{user['id']}",
            )
        except JobOperationError as exc:
            raise_job_operation_error(exc)
        except JobServiceError as exc:
            raise_job_http_error(exc)
        return ApprovalDecisionResponse(**decision)

    @router.get(
        "/workspaces/{workspace_id}/jobs/{job_id}/approvals",
        response_model=ApprovalDecisionListResponse,
    )
    def list_approval_decisions(workspace_id: str, job_id: str) -> ApprovalDecisionListResponse:
        require_workflows_enabled(settings)
        try:
            decisions = approvals.list_decisions(workspace_id, job_id)
        except JobServiceError as exc:
            raise_job_http_error(exc)
        return ApprovalDecisionListResponse.model_validate({"decisions": decisions})

    return router
