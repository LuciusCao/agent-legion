from fastapi import APIRouter

from server.app.routes.job_contracts import (
    GlobalServicesResponse,
    PipelineResponse,
    PipelinesListResponse,
    ResourceProvidersResponse,
)
from server.app.routes.job_http import raise_job_http_error, require_pipelines_enabled
from server.app.services.job_errors import JobServiceError
from server.app.services.pipeline_catalog import PipelineCatalogService
from server.app.settings import Settings


def create_pipeline_catalog_router(
    service: PipelineCatalogService, settings: Settings
) -> APIRouter:
    router = APIRouter()

    @router.get("/resource-providers", response_model=ResourceProvidersResponse)
    def get_resource_providers() -> ResourceProvidersResponse:
        require_pipelines_enabled(settings)
        return ResourceProvidersResponse(providers=service.resource_providers())

    @router.get("/global-services", response_model=GlobalServicesResponse)
    def get_global_services() -> GlobalServicesResponse:
        return GlobalServicesResponse(**service.global_services())

    @router.get("/pipelines", response_model=PipelinesListResponse)
    def list_pipelines() -> PipelinesListResponse:
        require_pipelines_enabled(settings)
        return PipelinesListResponse(pipelines=service.list_pipelines())

    @router.get("/pipelines/{pipeline_key}", response_model=PipelineResponse)
    def get_pipeline(pipeline_key: str) -> PipelineResponse:
        require_pipelines_enabled(settings)
        try:
            return PipelineResponse(pipeline=service.pipeline(pipeline_key))
        except JobServiceError as exc:
            raise_job_http_error(exc)

    return router
