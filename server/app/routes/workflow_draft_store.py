"""Studio workflow YAML draft store routes (schema v61).

Mounted under the studio_secured() surface via create_workflow_revisions_router
(require_workspace_access + require_studio_authoring), alongside
workflow-drafts/validate|compare|publish. The PUT is an effecting write and
mounts reject_studio_agent_scope (STUDIO-AGENT-001): the studio-agent tool
surface has no draft-store tool, so a scoped run token has no business
rewriting the human editor's draft. The GET stays on the plain secured
surface (same convention as the workflow-revisions reads: the scoped token
authenticates as the initiating user and may read what they can read).
#633 codex review P1-1 + #1226 五轮收口：PUT 携带 base_generation 时是代次
CAS（落后即 409，detail 携带当前草稿状态与代次）；缺省保留 legacy
last-write-wins 放行（设计 v2 记明的兼容残余）。
"""

from typing import Any

from fastapi import APIRouter, Depends

from server.app.auth.dependencies import reject_studio_agent_scope
from server.app.jobs import JobQueries
from server.app.routes.workflow_draft_store_contracts import (
    WorkflowDraftConflictResponse,
    WorkflowDraftStoreRequest,
    WorkflowDraftStoreResponse,
)
from server.app.services.workflow_draft_cas import save_workflow_draft_if_unchanged
from server.app.services.workflow_draft_store import get_workflow_draft, save_workflow_draft

# 409 CAS 冲突的响应契约（#1177 codex P1/P2）：detail 由 app 级异常处理器
# 从 DraftConflictError.payload 渲染，FastAPI 原生 handler 固定外包一层
# detail——responses= 挂带封套的响应模型进 OpenAPI，前端 transport type
# 从生成的 api.ts 派生（不再手写）。
_DRAFT_CONFLICT_RESPONSES: dict[int | str, dict[str, Any]] = {
    409: {"model": WorkflowDraftConflictResponse, "description": "Stale base generation"}
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
            # 空态也携带当前代次（发布删行后的首存以它为 base）。
            return WorkflowDraftStoreResponse(
                generation=job_db.get_workspace_workflow_draft_generation(workspace_id)
            )
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
        # 代次 CAS（#633 P1-1 + #1226 五轮收口）：带 base_generation 的 PUT
        # 落后即 409；缺省为 legacy 无代次 LWW（兼容残余，现行客户端都带）。
        if request.base_generation is None:
            draft = save_workflow_draft(job_db, workspace_id, request.definition_yaml)
        else:
            draft = save_workflow_draft_if_unchanged(
                job_db,
                workspace_id,
                request.definition_yaml,
                request.base_generation,
            )
        return WorkflowDraftStoreResponse.model_validate(draft)

    return router
