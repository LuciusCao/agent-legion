"""Batch approval endpoints (#1363), sharing the job-batch accept path."""

from __future__ import annotations

import uuid
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from server.app.auth.dependencies import reject_studio_agent_scope, require_user
from server.app.routes.job_batch_filter_contracts import JobSelectionMixin
from server.app.routes.job_batch_operations import ACCEPTED_202, async_dispatch_or_none
from server.app.services import approval_batch
from server.app.services.approval_decisions import ApprovalDecisionService


class BatchApprovalJobsRequest(JobSelectionMixin):
    note: str = Field(default="", max_length=20_000)


class ApprovalBatchResultResponse(BaseModel):
    job_id: str
    operation: Literal["approve", "reject"]
    status: Literal["succeeded", "skipped", "failed"]
    node_key: str | None = None
    reason_code: str | None = None
    message: str | None = None


class BatchApprovalJobsResponse(BaseModel):
    results: list[ApprovalBatchResultResponse]


def create_job_batch_approval_router(approvals: ApprovalDecisionService) -> APIRouter:
    router = APIRouter(dependencies=[Depends(reject_studio_agent_scope)])

    def _decide(
        workspace_id: str,
        payload: BatchApprovalJobsRequest,
        user: dict[str, Any],
        *,
        kind: Literal["approve", "reject"],
    ) -> BatchApprovalJobsResponse | JSONResponse:
        actor = f"user:{user['id']}"
        batch_id = uuid.uuid4().hex
        selection = "filter" if payload.filter is not None else "ids"
        ids, accepted = async_dispatch_or_none(
            approvals.job_db,
            workspace_id,
            kind,
            payload,
            {
                "note": payload.note,
                "decided_by": actor,
                "batch_id": batch_id,
                "batch_selection": selection,
            },
            user,
            operation_id=batch_id,
        )
        if accepted is not None:
            return accepted
        results = approval_batch.batch_decide(
            approvals,
            workspace_id,
            ids,
            verdict="approved" if kind == "approve" else "rejected",
            note=payload.note,
            decided_by=actor,
            batch_id=batch_id,
            batch_selection=selection,
        )
        return BatchApprovalJobsResponse(
            results=[ApprovalBatchResultResponse.model_validate(result) for result in results]
        )

    @router.post(
        "/workspaces/{workspace_id}/jobs/batch-approve",
        response_model=BatchApprovalJobsResponse,
        responses=ACCEPTED_202,
    )
    def batch_approve(
        workspace_id: str,
        payload: BatchApprovalJobsRequest,
        user: Annotated[dict[str, Any], Depends(require_user)],
    ) -> BatchApprovalJobsResponse | JSONResponse:
        return _decide(workspace_id, payload, user, kind="approve")

    @router.post(
        "/workspaces/{workspace_id}/jobs/batch-reject",
        response_model=BatchApprovalJobsResponse,
        responses=ACCEPTED_202,
    )
    def batch_reject(
        workspace_id: str,
        payload: BatchApprovalJobsRequest,
        user: Annotated[dict[str, Any], Depends(require_user)],
    ) -> BatchApprovalJobsResponse | JSONResponse:
        return _decide(workspace_id, payload, user, kind="reject")

    return router
