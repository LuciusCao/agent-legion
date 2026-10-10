"""Studio-agent workflow draft-store tool endpoints (#633).

Read (``GET .../workflow/draft``) and CAS write (``PUT .../workflow/draft``)
for the workspace's Studio canvas draft — the SAME draft row the human
editor autosaves to. Draft-only by design: publishing stays a human action
on the guarded surface (STUDIO-AGENT-001); the write is compare-and-set,
so a stale ``expected_updated_at`` is a 409 carrying the current draft
rather than a silent overwrite of the human's newer edit. The endpoints
are workspace-bound and mount on the workspace_scoped router inside
``studio_agent_tools.create_studio_agent_tools_router``, split out here
for the file-size budget (same pattern as ``studio_agent_prompt_tools``).
"""

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field, field_validator

from server.app.jobs import JobQueries
from server.app.routes.workflow_draft_store_contracts import (
    WorkflowDraftConflictDetail,
)
from server.app.services.workflow_draft_cas import save_workflow_draft_if_unchanged
from server.app.services.workflow_draft_cas_token import (
    CAS_TIMESTAMP_HINT,
    parse_cas_timestamp,
)
from server.app.services.workflow_draft_store import get_workflow_draft

# 409 CAS 冲突的响应契约（#1177 codex P1）：与人侧 draft-store PUT 同一
# detail 形状（服务层同一 DraftConflictError.payload 渲染），contracts 立
# 模型并经 responses= 进 OpenAPI。
_DRAFT_CONFLICT_RESPONSES: dict[int | str, dict[str, Any]] = {
    409: {"model": WorkflowDraftConflictDetail, "description": "Stale CAS base"}
}


# #633 CAS draft save: expected_updated_at is the updated_at the last read
# returned (or the literal "never-saved"); a stale value is a 409 carrying the
# current draft, never a silent overwrite. A blank draft is refused like the
# human editor's store (a whitespace-only draft would resurrect broken).
# #633 codex review P2-2: a token that is neither the marker nor a parseable
# ISO timestamp is a 422 (never reaches the timestamptz cast as a 500).
class StudioAgentWorkflowDraftSaveRequest(BaseModel):
    definition_yaml: str
    expected_updated_at: str = Field(min_length=1)

    @field_validator("definition_yaml")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("definition_yaml must not be blank")
        return value

    @field_validator("expected_updated_at")
    @classmethod
    def _cas_timestamp_or_never_saved(cls, value: str) -> str:
        if parse_cas_timestamp(value):
            return value
        raise ValueError(CAS_TIMESTAMP_HINT)


class StudioAgentWorkflowDraftResponse(BaseModel):
    """Human draft-store mirror; both null when no draft (structured empty)."""

    definition_yaml: str | None = None
    updated_at: str | None = None
    # #1143（方案 B）：草稿语义身份 hash（不可解析 → None），与人类面
    # WorkflowDraftStoreResponse 同源——保存/读取响应带身份供卡核对。
    definition_hash: str | None = None


def _draft_response(draft: dict) -> StudioAgentWorkflowDraftResponse:
    return StudioAgentWorkflowDraftResponse(
        definition_yaml=draft["definition_yaml"],
        updated_at=str(draft["updated_at"]),
        definition_hash=draft.get("definition_hash"),
    )


def create_studio_agent_draft_tools_router(job_db: JobQueries) -> APIRouter:
    router = APIRouter()

    @router.get(
        "/studio-agent/tools/workspaces/{workspace_id}/workflow/draft",
        response_model=StudioAgentWorkflowDraftResponse,
    )
    def get_workflow_draft_route(workspace_id: str) -> StudioAgentWorkflowDraftResponse:
        draft = get_workflow_draft(job_db, workspace_id)
        if draft is None:
            return StudioAgentWorkflowDraftResponse()
        return _draft_response(draft)

    @router.put(
        "/studio-agent/tools/workspaces/{workspace_id}/workflow/draft",
        response_model=StudioAgentWorkflowDraftResponse,
        responses=_DRAFT_CONFLICT_RESPONSES,
    )
    def save_workflow_draft_route(
        workspace_id: str, payload: StudioAgentWorkflowDraftSaveRequest
    ) -> StudioAgentWorkflowDraftResponse:
        draft = save_workflow_draft_if_unchanged(
            job_db, workspace_id, payload.definition_yaml, payload.expected_updated_at
        )
        return _draft_response(draft)

    return router
