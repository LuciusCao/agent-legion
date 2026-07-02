from fastapi import APIRouter

import server.app.routes.job_contracts as job_contracts
from server.app.routes.job_http import require_workflows_enabled
from server.app.services.workflow_catalog import WorkflowCatalogService
from server.app.settings import Settings


def create_workflow_resource_providers_router(
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

    return router
