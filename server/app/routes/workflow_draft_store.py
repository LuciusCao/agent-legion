"""Studio workflow YAML draft store routes (schema v61).

Mounted under the studio_secured() surface via create_workflow_revisions_router
(require_workspace_access + require_studio_authoring), alongside
workflow-drafts/validate|compare|publish. The PUT is an effecting write and
mounts reject_studio_agent_scope (STUDIO-AGENT-001): the studio-agent tool
surface has no draft-store tool, so a scoped run token has no business
rewriting the human editor's draft. The GET stays on the plain secured
surface (same convention as the workflow-revisions reads: the scoped token
authenticates as the initiating user and may read what they can read).
"""

from fastapi import APIRouter, Depends

from server.app.auth.dependencies import reject_studio_agent_scope
from server.app.jobs import JobQueries
from server.app.routes.job_http import raise_job_http_error, require_workflows_enabled
from server.app.routes.workflow_draft_store_contracts import (
    WorkflowDraftStoreRequest,
    WorkflowDraftStoreResponse,
)
from server.app.services.job_errors import JobServiceError
from server.app.services.workflow_draft_store import get_workflow_draft, save_workflow_draft
from server.app.settings import Settings


def create_workflow_draft_store_router(job_db: JobQueries, settings: Settings) -> APIRouter:
    router = APIRouter()

    @router.get(
        "/workspaces/{workspace_id}/workflow-draft",
        response_model=WorkflowDraftStoreResponse,
    )
    def get_draft(workspace_id: str) -> WorkflowDraftStoreResponse:
        require_workflows_enabled(settings)
        try:
            draft = get_workflow_draft(job_db, workspace_id)
        except JobServiceError as exc:
            raise_job_http_error(exc)
        if draft is None:
            return WorkflowDraftStoreResponse()
        return WorkflowDraftStoreResponse.model_validate(draft)

    @router.put(
        "/workspaces/{workspace_id}/workflow-draft",
        response_model=WorkflowDraftStoreResponse,
        dependencies=[Depends(reject_studio_agent_scope)],
    )
    def put_draft(
        workspace_id: str, request: WorkflowDraftStoreRequest
    ) -> WorkflowDraftStoreResponse:
        require_workflows_enabled(settings)
        try:
            draft = save_workflow_draft(job_db, workspace_id, request.definition_yaml)
        except JobServiceError as exc:
            raise_job_http_error(exc)
        return WorkflowDraftStoreResponse.model_validate(draft)

    return router
