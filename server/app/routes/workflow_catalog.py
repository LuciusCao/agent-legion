from fastapi import APIRouter

import server.app.routes.job_contracts as job_contracts
import server.app.routes.workflow_contracts as workflow_contracts
from server.app.routes.job_http import raise_job_http_error, require_workflows_enabled
from server.app.services.job_errors import JobServiceError
from server.app.services.workflow_catalog import WorkflowCatalogService
from server.app.settings import Settings


def create_workflow_catalog_router(
    service: WorkflowCatalogService, settings: Settings
) -> APIRouter:
    router = APIRouter()

    @router.get("/resource-providers", response_model=job_contracts.ResourceProvidersResponse)
    def get_resource_providers() -> job_contracts.ResourceProvidersResponse:
        require_workflows_enabled(settings)
        return job_contracts.ResourceProvidersResponse(providers=service.resource_providers())

    @router.get("/global-services", response_model=job_contracts.GlobalServicesResponse)
    def get_global_services() -> job_contracts.GlobalServicesResponse:
        return job_contracts.GlobalServicesResponse(**service.global_services())

    @router.get("/workflows", response_model=workflow_contracts.WorkflowsListResponse)
    def list_workflows() -> workflow_contracts.WorkflowsListResponse:
        require_workflows_enabled(settings)
        return workflow_contracts.workflows_list_response(service.list_workflows())

    @router.get("/workflows/{workflow_key}", response_model=workflow_contracts.WorkflowResponse)
    def get_workflow(workflow_key: str) -> workflow_contracts.WorkflowResponse:
        require_workflows_enabled(settings)
        try:
            return workflow_contracts.workflow_response(service.workflow(workflow_key))
        except JobServiceError as exc:
            raise_job_http_error(exc)

    return router
