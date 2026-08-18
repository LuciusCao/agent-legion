from fastapi import APIRouter, Depends, HTTPException

from ..auth.dependencies import reject_studio_agent_scope
from ..services.job_packages import JobPackageService
from ..services.job_selection_resolver import EmptyJobSelectionError
from .package_contracts import (
    WorkspacePackageRequest,
    WorkspacePackageResultResponse,
    WorkspacePackageStatusResetResponse,
)


def register_clear_packed_route(router: APIRouter, job_packages: JobPackageService) -> None:
    @router.post(
        "/workspaces/{workspace_id}/jobs/clear-packed",
        response_model=WorkspacePackageStatusResetResponse,
        dependencies=[Depends(reject_studio_agent_scope)],
    )
    def clear_workspace_jobs_packed_status(
        workspace_id: str, request: WorkspacePackageRequest
    ) -> WorkspacePackageStatusResetResponse:
        try:
            results = job_packages.clear_packed_status(
                workspace_id,
                request.job_ids,
                job_filter=request.resolved_filter(),
                exclude_ids=request.exclude_ids,
            )
        except EmptyJobSelectionError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None
        succeeded_count = sum(result["status"] == "succeeded" for result in results)
        return WorkspacePackageStatusResetResponse(
            results=[WorkspacePackageResultResponse.model_validate(result) for result in results],
            succeeded_count=succeeded_count,
            failed_count=len(results) - succeeded_count,
        )
