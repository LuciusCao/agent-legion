from fastapi import APIRouter, HTTPException

from ..services.job_packages import JobPackageService
from .package_contracts import (
    WorkspacePackageRequest,
    WorkspacePackageResultResponse,
    WorkspacePackageStatusResetResponse,
)


def register_clear_packed_route(router: APIRouter, job_packages: JobPackageService) -> None:
    @router.post(
        "/workspaces/{workspace_id}/jobs/clear-packed",
        response_model=WorkspacePackageStatusResetResponse,
    )
    def clear_workspace_jobs_packed_status(
        workspace_id: str, request: WorkspacePackageRequest
    ) -> WorkspacePackageStatusResetResponse:
        if not request.job_ids:
            raise HTTPException(status_code=400, detail="No job_ids provided")
        results = job_packages.clear_packed_status(workspace_id, request.job_ids)
        succeeded_count = sum(result["status"] == "succeeded" for result in results)
        return WorkspacePackageStatusResetResponse(
            results=[WorkspacePackageResultResponse.model_validate(result) for result in results],
            succeeded_count=succeeded_count,
            failed_count=len(results) - succeeded_count,
        )
