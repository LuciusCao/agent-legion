"""Studio workflow YAML draft store routes (schema v61).

Mounted under the studio_secured() surface via create_workflow_revisions_router
(require_workspace_access + require_studio_authoring), alongside
workflow-drafts/validate|compare|publish. The PUT is an effecting write and
mounts reject_studio_agent_scope (STUDIO-AGENT-001): the studio-agent tool
surface has no draft-store tool, so a scoped run token has no business
rewriting the human editor's draft. The GET stays on the plain secured
surface (same convention as the workflow-revisions reads: the scoped token
authenticates as the initiating user and may read what they can read).
#633 codex review P1-1: the PUT carries the CAS base when the client sends
expected_updated_at (stale base → 409 with the current draft, mirroring the
tool surface); an absent field keeps the legacy last-write-wins upsert.
"""

from fastapi import APIRouter, Depends

from server.app.auth.dependencies import reject_studio_agent_scope
from server.app.jobs import JobQueries
from server.app.routes.job_http import raise_job_http_error
from server.app.routes.workflow_draft_store_contracts import (
    WorkflowDraftStoreRequest,
    WorkflowDraftStoreResponse,
)
from server.app.services.job_errors import JobServiceError
from server.app.services.workflow_draft_cas import save_workflow_draft_if_unchanged
from server.app.services.workflow_draft_store import get_workflow_draft, save_workflow_draft


def create_workflow_draft_store_router(job_db: JobQueries) -> APIRouter:
    router = APIRouter()

    @router.get(
        "/workspaces/{workspace_id}/workflow-draft",
        response_model=WorkflowDraftStoreResponse,
    )
    def get_draft(workspace_id: str) -> WorkflowDraftStoreResponse:
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
        # #633 codex review P1-1: with expected_updated_at the PUT is a real
        # CAS save (a stale base is a 409 carrying the current draft — same
        # payload shape as the tool surface); without it, the legacy
        # last-write-wins upsert keeps the documented two-tab autosave
        # semantics for old clients.
        try:
            if request.expected_updated_at is None:
                draft = save_workflow_draft(job_db, workspace_id, request.definition_yaml)
            else:
                draft = save_workflow_draft_if_unchanged(
                    job_db,
                    workspace_id,
                    request.definition_yaml,
                    request.expected_updated_at,
                )
        except JobServiceError as exc:
            raise_job_http_error(exc)
        return WorkflowDraftStoreResponse.model_validate(draft)

    return router
