from fastapi import APIRouter, Depends

from server.app.auth.dependencies import reject_studio_agent_scope
from server.app.routes.job_contracts import JobBatchRequest, JobBatchResponse
from server.app.routes.job_http import raise_job_http_error, require_workflows_enabled
from server.app.services.job_errors import JobServiceError
from server.app.services.job_intake import JobIntakeService
from server.app.settings import Settings


def create_job_batches_router(service: JobIntakeService, settings: Settings) -> APIRouter:
    router = APIRouter()

    def create(workspace_id: str, payload: JobBatchRequest) -> JobBatchResponse:
        require_workflows_enabled(settings)
        try:
            return JobBatchResponse(**service.create_batch(workspace_id, payload.model_dump()))
        except JobServiceError as exc:
            raise_job_http_error(exc)

    @router.post(
        "/workspaces/{workspace_id}/job-batches",
        response_model=JobBatchResponse,
        dependencies=[Depends(reject_studio_agent_scope)],
    )
    def create_workspace_job_batch(workspace_id: str, payload: JobBatchRequest) -> JobBatchResponse:
        return create(workspace_id, payload)

    return router
