from fastapi import APIRouter

from server.app.routes import job_contracts, pipeline_contracts
from server.app.routes.job_http import raise_job_http_error, require_pipelines_enabled
from server.app.services.job_errors import JobServiceError
from server.app.services.pipeline_catalog import PipelineCatalogService
from server.app.settings import Settings


def create_pipeline_catalog_router(
    service: PipelineCatalogService, settings: Settings
) -> APIRouter:
    router = APIRouter()

    @router.get("/resource-providers", response_model=job_contracts.ResourceProvidersResponse)
    def get_resource_providers() -> job_contracts.ResourceProvidersResponse:
        require_pipelines_enabled(settings)
        return job_contracts.ResourceProvidersResponse(providers=service.resource_providers())

    @router.get("/global-services", response_model=job_contracts.GlobalServicesResponse)
    def get_global_services() -> job_contracts.GlobalServicesResponse:
        return job_contracts.GlobalServicesResponse(**service.global_services())

    @router.get("/pipelines", response_model=pipeline_contracts.PipelinesListResponse)
    def list_pipelines() -> pipeline_contracts.PipelinesListResponse:
        require_pipelines_enabled(settings)
        return pipeline_contracts.pipelines_list_response(service.list_pipelines())

    @router.get("/pipelines/{pipeline_key}", response_model=pipeline_contracts.PipelineResponse)
    def get_pipeline(pipeline_key: str) -> pipeline_contracts.PipelineResponse:
        require_pipelines_enabled(settings)
        try:
            return pipeline_contracts.pipeline_response(service.pipeline(pipeline_key))
        except JobServiceError as exc:
            raise_job_http_error(exc)

    return router
