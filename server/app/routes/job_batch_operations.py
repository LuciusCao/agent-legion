"""Batch-ops async queueing (#946): 202 handle contract + the read surface.

The 8 batch job endpoints return the accepted-operation handle (202) when the
selection exceeds the sync threshold; this module carries that response model
plus the polling endpoints (status + keyset-paged per-job results) the UI
progress view consumes. Mounted inside the job route group from
``jobs.py`` (member-level workspace guard); the write-side submission stays
on the effecting routers with their studio-agent refusals.
"""

from __future__ import annotations

import json
from typing import Annotated, Any, Literal

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from server.app.jobs import JobQueries
from server.app.services.job_batch_operations import (
    dispatch_batch_selection,
    get_operation_for_workspace,
    operation_record,
)

JobBatchOperationStatus = Literal[
    "pending", "running", "paused", "completed", "failed", "cancelled"
]


JobBatchOperationKindParam = Literal[
    "rerun",
    "rerun_by_failure",
    "delete",
    "run_to",
    "pause",
    "resume",
    "upgrade_workflow",
    "package",
    "clear_packed",
]


class JobBatchOperationResponse(BaseModel):
    """The public operation handle (accept response + status poll)."""

    id: str
    workspace_id: str
    kind: JobBatchOperationKindParam
    status: JobBatchOperationStatus
    items_total: int = Field(ge=0)
    processed_count: int = Field(ge=0)
    succeeded_count: int = Field(ge=0)
    skipped_count: int = Field(ge=0)
    failed_count: int = Field(ge=0)
    # reason_code → count over non-succeeded results (e.g. {"not_found": 3}).
    reason_counts: dict[str, int] = Field(default_factory=dict)
    # Kind-specific terminal output (package: package_filename/download_url).
    output: dict[str, Any] = Field(default_factory=dict)
    chunk_errors: list[dict[str, Any]] = Field(default_factory=list)
    error_message: str = ""
    created_by: str = ""
    created_at: str | None = None
    updated_at: str | None = None
    finished_at: str | None = None


class JobBatchOperationAcceptedResponse(BaseModel):
    """202 body of every async-dispatched batch endpoint."""

    operation: JobBatchOperationResponse


#: Declared on every async-capable batch endpoint so the exported OpenAPI
#: keeps the sync 200 $ref and gains a named 202 $ref (the export gate rejects
#: inline union schemas).
ACCEPTED_202: dict[int | str, dict[str, Any]] = {202: {"model": JobBatchOperationAcceptedResponse}}


def accepted_202_response(job_db: JobQueries, operation: dict[str, Any]) -> JSONResponse:
    """The 202 accept body, validated by construction (the response_model on
    the route stays the sync model, so the async branch returns a Response)."""
    model = JobBatchOperationAcceptedResponse(
        operation=JobBatchOperationResponse.model_validate(operation_record(job_db, operation))
    )
    return JSONResponse(status_code=202, content=model.model_dump(mode="json"))


def async_dispatch_or_none(
    job_db: JobQueries,
    workspace_id: str,
    kind: str,
    payload: Any,
    params: dict[str, Any],
    user: dict[str, Any],
) -> tuple[list[str], JSONResponse | None]:
    """Route-side dispatch for the async-capable batch endpoints (#946):
    (resolved ids, 202 response | None). The sync branch consumes the ids.
    """
    ids, operation = dispatch_batch_selection(
        job_db,
        workspace_id,
        kind,
        payload.job_ids,
        payload.resolved_filter(),
        payload.exclude_ids,
        params,
        f"user:{user['id']}",
    )
    return ids, accepted_202_response(job_db, operation) if operation is not None else None


class JobBatchOperationResultItem(BaseModel):
    seq: int = Field(ge=0)
    # The per-job result dict exactly as the sync endpoint would return it
    # (rerun: node_key; rerun_by_failure: rerun_nodes; upgrade: counts;
    # package: no operation field) — the payload column replays it verbatim.
    result: dict[str, Any]


class JobBatchOperationResultsResponse(BaseModel):
    results: list[JobBatchOperationResultItem]
    # Keyset cursor: the last seq of this page; None when the page is short.
    next_cursor: str | None = None


def create_job_batch_operations_router(job_db: JobQueries) -> APIRouter:
    router = APIRouter()

    def _operation_or_404(workspace_id: str, op_id: str) -> dict[str, Any]:
        row = get_operation_for_workspace(job_db, workspace_id, op_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Job batch operation not found")
        return row

    @router.get(
        "/workspaces/{workspace_id}/job-batch-operations/{op_id}",
        response_model=JobBatchOperationResponse,
    )
    def get_job_batch_operation(workspace_id: str, op_id: str) -> JobBatchOperationResponse:
        return JobBatchOperationResponse.model_validate(
            operation_record(job_db, _operation_or_404(workspace_id, op_id))
        )

    @router.get(
        "/workspaces/{workspace_id}/job-batch-operations/{op_id}/results",
        response_model=JobBatchOperationResultsResponse,
    )
    def list_job_batch_operation_results(
        workspace_id: str,
        op_id: str,
        limit: Annotated[int, Query(ge=1, le=1000)] = 500,
        cursor: Annotated[int | None, Query(ge=0)] = None,
    ) -> JobBatchOperationResultsResponse:
        _operation_or_404(workspace_id, op_id)
        # limit+1 probe: the extra row decides next_cursor before the cut
        # (the /jobs list endpoint's #735 precedent).
        rows = job_db.list_job_batch_operation_results_page(
            op_id, limit=limit + 1, after_seq=cursor
        )
        page = rows[:limit]
        next_cursor = str(page[-1]["seq"]) if len(rows) > limit and page else None
        return JobBatchOperationResultsResponse(
            results=[
                JobBatchOperationResultItem(
                    seq=int(row["seq"]),
                    result=json.loads(str(row["payload_json"] or "{}")),
                )
                for row in page
            ],
            next_cursor=next_cursor,
        )

    return router
