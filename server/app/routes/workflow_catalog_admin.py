"""Admin-only workflow catalog registration (DB-WORKFLOW-CATALOG-001).

Registering a workflow key is a platform-global change, so it sits behind
``require_admin`` like the other global admin endpoints; the read endpoints
stay on the workspace-access secured catalog router.
"""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

import server.app.routes.workflow_contracts as workflow_contracts
from server.app.auth.dependencies import require_admin
from server.app.routes.job_http import raise_job_http_error, require_workflows_enabled
from server.app.scheduler_wakeup import notify_schedulable_work, reload_scan_entries_best_effort
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
        request: Request,
        payload: WorkflowRegisterRequest,
        _admin: Annotated[dict[str, Any], Depends(require_admin)],
    ) -> workflow_contracts.WorkflowRegisteredResponse:
        require_workflows_enabled(settings)
        try:
            entry = service.register(payload.key, payload.label, payload.description)
        except JobServiceError as exc:
            raise_job_http_error(exc)
        # The catalog row is committed at this point. Refresh the running
        # worker's scan list so the new key is scanned without a restart,
        # then wake the poll loop (process-local, like the S0a executor
        # hot-reload trigger). Best-effort: a reload failure must not 500
        # the committed write; the poll loop reconcile self-heals.
        worker = getattr(request.app.state, "workflow_worker", None)
        if worker is not None:
            reload_scan_entries_best_effort(worker)
        notify_schedulable_work()
        return workflow_contracts.WorkflowRegisteredResponse.model_validate(entry)

    return router
