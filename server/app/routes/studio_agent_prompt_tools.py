"""Studio-agent node prompt tool endpoints.

Read/preview (``POST .../node-prompt``) and draft write
(``PUT .../node-prompt``) for a workflow node's ``execution.prompt``. Both are
draft-only by design: the save edits the workspace's unpublished draft YAML —
publishing stays a human action in Studio (STUDIO-AGENT-001). The endpoints
are workspace-bound, so they mount on the workspace_scoped router inside
``studio_agent_tools.create_studio_agent_tools_router`` (scoped token +
workspace binding), split out here for the file-size budget.
"""

from fastapi import APIRouter

from server.app.jobs import JobQueries
from server.app.routes.workflow_node_prompt_contracts import (
    NodePromptPreviewRequest,
    NodePromptPreviewResponse,
    NodePromptSaveRequest,
    NodePromptSaveResponse,
)
from server.app.services.node_prompt_preview import preview_node_prompt, save_node_prompt


def create_studio_agent_prompt_tools_router(job_db: JobQueries) -> APIRouter:
    router = APIRouter()

    @router.post(
        "/studio-agent/tools/workspaces/{workspace_id}/node-prompt",
        response_model=NodePromptPreviewResponse,
    )
    def get_node_prompt(
        workspace_id: str, payload: NodePromptPreviewRequest
    ) -> NodePromptPreviewResponse:
        result = preview_node_prompt(
            job_db, workspace_id, payload.node_key, payload.definition_yaml
        )
        return NodePromptPreviewResponse(**result)

    @router.put(
        "/studio-agent/tools/workspaces/{workspace_id}/node-prompt",
        response_model=NodePromptSaveResponse,
    )
    def save_node_prompt_route(
        workspace_id: str, payload: NodePromptSaveRequest
    ) -> NodePromptSaveResponse:
        result = save_node_prompt(
            job_db,
            workspace_id,
            payload.node_key,
            payload.prompt,
            prompt_mode=payload.prompt_mode,
        )
        return NodePromptSaveResponse(**result)

    return router
