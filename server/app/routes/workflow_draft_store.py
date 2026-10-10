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

from typing import Any

from fastapi import APIRouter, Depends

from server.app.auth.dependencies import reject_studio_agent_scope
from server.app.jobs import JobQueries
from server.app.routes.workflow_draft_store_contracts import (
    WorkflowDraftConflictDetail,
    WorkflowDraftStoreRequest,
    WorkflowDraftStoreResponse,
)
from server.app.services.workflow_draft_cas import save_workflow_draft_if_unchanged
from server.app.services.workflow_draft_store import get_workflow_draft, save_workflow_draft

# 409 CAS 冲突的响应契约（#1177 codex P1）：detail 由 app 级异常处理器
# 从 DraftConflictError.payload 渲染，形状在 contracts 立模型并经
# responses= 进 OpenAPI——前端 transport type 从生成的 api.ts 派生
# （不再手写）。
_DRAFT_CONFLICT_RESPONSES: dict[int | str, dict[str, Any]] = {
    409: {"model": WorkflowDraftConflictDetail, "description": "Stale CAS base"}
}


def create_workflow_draft_store_router(job_db: JobQueries) -> APIRouter:
    router = APIRouter()

    @router.get(
        "/workspaces/{workspace_id}/workflow-draft",
        response_model=WorkflowDraftStoreResponse,
    )
    def get_draft(workspace_id: str) -> WorkflowDraftStoreResponse:
        draft = get_workflow_draft(job_db, workspace_id)
        if draft is None:
            return WorkflowDraftStoreResponse()
        return WorkflowDraftStoreResponse.model_validate(draft)

    @router.put(
        "/workspaces/{workspace_id}/workflow-draft",
        response_model=WorkflowDraftStoreResponse,
        responses=_DRAFT_CONFLICT_RESPONSES,
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
        if request.expected_updated_at is None:
            draft = save_workflow_draft(job_db, workspace_id, request.definition_yaml)
        else:
            draft = save_workflow_draft_if_unchanged(
                job_db,
                workspace_id,
                request.definition_yaml,
                request.expected_updated_at,
            )
        return WorkflowDraftStoreResponse.model_validate(draft)

    return router
