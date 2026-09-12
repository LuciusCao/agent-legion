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

from fastapi import APIRouter
from pydantic import BaseModel, Field, field_validator

from server.app.jobs import JobQueries
from server.app.routes.job_http import raise_job_http_error
from server.app.services.job_errors import JobServiceError
from server.app.services.workflow_draft_cas import save_workflow_draft_if_unchanged
from server.app.services.workflow_draft_store import get_workflow_draft


# #633 CAS draft save: expected_updated_at is the updated_at the last read
# returned (or the literal "never-saved"); a stale value is a 409 carrying the
# current draft, never a silent overwrite. A blank draft is refused like the
# human editor's store (a whitespace-only draft would resurrect broken).
class StudioAgentWorkflowDraftSaveRequest(BaseModel):
    definition_yaml: str
    expected_updated_at: str = Field(min_length=1)

    @field_validator("definition_yaml")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("definition_yaml must not be blank")
        return value


class StudioAgentWorkflowDraftResponse(BaseModel):
    """Human draft-store mirror; both null when no draft (structured empty)."""

    definition_yaml: str | None = None
    updated_at: str | None = None


def _draft_response(draft: dict) -> StudioAgentWorkflowDraftResponse:
    return StudioAgentWorkflowDraftResponse(
        definition_yaml=draft["definition_yaml"],
        updated_at=str(draft["updated_at"]),
    )


def create_studio_agent_draft_tools_router(job_db: JobQueries) -> APIRouter:
    router = APIRouter()

    @router.get(
        "/studio-agent/tools/workspaces/{workspace_id}/workflow/draft",
        response_model=StudioAgentWorkflowDraftResponse,
    )
    def get_workflow_draft_route(workspace_id: str) -> StudioAgentWorkflowDraftResponse:
        try:
            draft = get_workflow_draft(job_db, workspace_id)
        except JobServiceError as exc:
            raise_job_http_error(exc)
        if draft is None:
            return StudioAgentWorkflowDraftResponse()
        return _draft_response(draft)

    @router.put(
        "/studio-agent/tools/workspaces/{workspace_id}/workflow/draft",
        response_model=StudioAgentWorkflowDraftResponse,
    )
    def save_workflow_draft_route(
        workspace_id: str, payload: StudioAgentWorkflowDraftSaveRequest
    ) -> StudioAgentWorkflowDraftResponse:
        try:
            draft = save_workflow_draft_if_unchanged(
                job_db, workspace_id, payload.definition_yaml, payload.expected_updated_at
            )
        except JobServiceError as exc:
            raise_job_http_error(exc)
        return _draft_response(draft)

    return router
