from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse

from ..auth.dependencies import reject_studio_agent_scope, require_user
from ..jobs import JobQueries
from ..services.job_packages import JobPackageService
from ..services.job_selection_resolver import EmptyJobSelectionError
from .job_batch_operations import ACCEPTED_202, async_dispatch_or_none
from .package_contracts import (
    WorkspacePackageRequest,
    WorkspacePackageResultResponse,
    WorkspacePackageStatusResetResponse,
)


def register_clear_packed_route(
    router: APIRouter, job_db: JobQueries, job_packages: JobPackageService
) -> None:
    @router.post(
        "/workspaces/{workspace_id}/jobs/clear-packed",
        response_model=WorkspacePackageStatusResetResponse,
        responses=ACCEPTED_202,
        dependencies=[Depends(reject_studio_agent_scope)],
    )
    def clear_workspace_jobs_packed_status(
        workspace_id: str,
        request: WorkspacePackageRequest,
        user: Annotated[dict[str, Any], Depends(require_user)],
    ) -> WorkspacePackageStatusResetResponse | JSONResponse:
        # #946: selections above the sync threshold enqueue (202 + handle).
        ids, accepted = async_dispatch_or_none(
            job_db, workspace_id, "clear_packed", request, {}, user
        )
        if accepted is not None:
            return accepted
        try:
            results = job_packages.clear_packed_status(workspace_id, ids)
        except EmptyJobSelectionError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None
        succeeded_count = sum(result["status"] == "succeeded" for result in results)
        return WorkspacePackageStatusResetResponse(
            results=[WorkspacePackageResultResponse.model_validate(result) for result in results],
            succeeded_count=succeeded_count,
            failed_count=len(results) - succeeded_count,
        )
