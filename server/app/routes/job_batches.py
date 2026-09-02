from fastapi import APIRouter, Depends

from server.app.auth.dependencies import reject_studio_agent_scope
from server.app.routes.job_contracts import JobBatchRequest, JobBatchResponse
from server.app.routes.job_http import (
    raise_job_http_error,
    reject_mismatched_workflow_key,
)
from server.app.services.job_errors import JobServiceError
from server.app.services.job_intake import JobIntakeService


def create_job_batches_router(service: JobIntakeService) -> APIRouter:
    router = APIRouter()

    def create(workspace_id: str, payload: JobBatchRequest) -> JobBatchResponse:
        # #211 Phase 2: absent workflow_key defaults to the path workspace_id
        # (equal since v62); explicit values keep flowing through verbatim.
        body = payload.model_dump()
        if body.get("workflow_key") is None:
            body["workflow_key"] = workspace_id
        # Codex P1 on #307: a mismatched explicit key would flow verbatim
        # into jobs rows (violating the v62 binding) — reject up front.
        reject_mismatched_workflow_key(workspace_id, body.get("workflow_key"))
        try:
            return JobBatchResponse(**service.create_batch(workspace_id, body))
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
