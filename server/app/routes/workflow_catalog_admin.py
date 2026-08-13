"""Admin-only workflow catalog registration (DB-WORKFLOW-CATALOG-001).

Registering a workflow key is a platform-global change, so it sits behind
``require_admin`` like the other global admin endpoints; the read endpoints
stay on the workspace-access secured catalog router.
"""

from typing import Annotated, Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel

import server.app.routes.workflow_contracts as workflow_contracts
from server.app.auth.dependencies import require_admin
from server.app.routes.job_http import raise_job_http_error, require_workflows_enabled
from server.app.services.job_errors import JobServiceError
from server.app.services.workflow_catalog import WorkflowCatalogService
from server.app.settings import Settings


class WorkflowRegisterRequest(BaseModel):
    key: str
    label: str
    description: str = ""


def create_workflow_catalog_admin_router(
    service: WorkflowCatalogService, settings: Settings
) -> APIRouter:
    router = APIRouter()

    @router.post("/workflows", response_model=workflow_contracts.WorkflowRegisteredResponse)
    def register_workflow(
        request: WorkflowRegisterRequest,
        _admin: Annotated[dict[str, Any], Depends(require_admin)],
    ) -> workflow_contracts.WorkflowRegisteredResponse:
        require_workflows_enabled(settings)
        try:
            entry = service.register(request.key, request.label, request.description)
        except JobServiceError as exc:
            raise_job_http_error(exc)
        return workflow_contracts.WorkflowRegisteredResponse.model_validate(entry)

    return router
