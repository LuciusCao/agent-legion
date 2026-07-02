from fastapi import APIRouter

import server.app.routes.workflow_contracts as workflow_contracts
from server.app.routes.job_http import raise_job_http_error, require_workflows_enabled
from server.app.services.job_errors import JobServiceError
from server.app.services.workflow_catalog import WorkflowCatalogService
from server.app.settings import Settings


def create_workflow_catalog_router(
    service: WorkflowCatalogService, settings: Settings
) -> APIRouter:
    router = APIRouter()

    @router.get("/workflows", response_model=workflow_contracts.WorkflowsListResponse)
    def list_workflows() -> workflow_contracts.WorkflowsListResponse:
        require_workflows_enabled(settings)
        return workflow_contracts.WorkflowsListResponse(
            workflows=[
                workflow_contracts.WorkflowSummaryResponse.model_validate(value)
                for value in service.list_workflows()
            ]
        )

    @router.get("/workflows/{workflow_key}", response_model=workflow_contracts.WorkflowResponse)
    def get_workflow(workflow_key: str) -> workflow_contracts.WorkflowResponse:
        require_workflows_enabled(settings)
        try:
            return workflow_contracts.WorkflowResponse(
                workflow=workflow_contracts.WorkflowDefinitionResponse.model_validate(
                    service.workflow(workflow_key)
                )
            )
        except JobServiceError as exc:
            raise_job_http_error(exc)

    return router
