from fastapi import APIRouter, Depends

from server.app.auth.dependencies import reject_studio_agent_scope
from server.app.routes.job_contracts import JobBatchRequest, JobBatchResponse
from server.app.services.job_intake import JobIntakeService


def create_job_batches_router(service: JobIntakeService) -> APIRouter:
    router = APIRouter()

    def create(workspace_id: str, payload: JobBatchRequest) -> JobBatchResponse:
        # #211 M3: the request no longer carries a workflow key — the path
        # workspace id is the workflow identifier. The intake payload keeps
        # its internal workflow_key member (it feeds the deterministic run id
        # digest, so identical resubmissions still dedupe across the upgrade);
        # a stray client value (extra="allow") is overwritten, never trusted.
        body = payload.model_dump()
        body["workflow_key"] = workspace_id
        return JobBatchResponse(**service.create_batch(workspace_id, body))

    @router.post(
        "/workspaces/{workspace_id}/job-batches",
        response_model=JobBatchResponse,
        dependencies=[Depends(reject_studio_agent_scope)],
    )
    def create_workspace_job_batch(workspace_id: str, payload: JobBatchRequest) -> JobBatchResponse:
        return create(workspace_id, payload)

    return router
