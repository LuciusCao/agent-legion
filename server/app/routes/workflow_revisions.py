import json

from fastapi import APIRouter, HTTPException

import server.app.routes.workflow_contracts as workflow_contracts
from server.app.jobs import JobQueries
from server.app.routes.studio_publish_requests import (
    create_studio_publish_request_router,
)
from server.app.routes.workflow_draft_compare import create_workflow_draft_compare_router
from server.app.routes.workflow_draft_publish import create_workflow_draft_publish_router
from server.app.routes.workflow_draft_store import create_workflow_draft_store_router
from server.app.routes.workflow_node_prompt_route import create_workflow_node_prompt_router
from server.app.routes.workflow_revisions_contracts import (
    ActiveWorkflowRevisionResponse,
    WorkflowRevisionDetailResponse,
    WorkflowRevisionsResponse,
    WorkflowRevisionSummary,
)
from server.app.services.workflow_revision_format import (
    definition_to_yaml,
    workflow_definition_to_response_payload,
)
from server.app.settings import Settings
from server.app.workflows.definition import workflow_definition_from_dict


def create_workflow_revisions_router(job_db: JobQueries, settings: Settings) -> APIRouter:
    router = APIRouter()

    @router.get(
        "/workspaces/{workspace_id}/workflow-revisions",
        response_model=WorkflowRevisionsResponse,
    )
    def list_workflow_revisions(workspace_id: str) -> WorkflowRevisionsResponse:
        workspace = job_db.get_workspace(workspace_id)
        if workspace is None:
            return WorkflowRevisionsResponse(revisions=[])
        workflow_key = str(workspace.get("default_workflow_key") or "")
        rows = job_db.list_workflow_revisions(workspace_id, workflow_key)
        # #211 M2: the column is gone from workflow_revisions (v70), but the
        # deprecated response field stays until the M3 window closes
        # (2026-10-31) — backfill it with the workspace's bound key.
        for row in rows:
            row.setdefault("workflow_key", workflow_key)
        return WorkflowRevisionsResponse(revisions=[WorkflowRevisionSummary(**row) for row in rows])

    @router.get(
        "/workspaces/{workspace_id}/workflow-revisions/active",
        response_model=ActiveWorkflowRevisionResponse,
    )
    def get_active_workflow_revision(workspace_id: str) -> ActiveWorkflowRevisionResponse:
        workspace = job_db.get_workspace(workspace_id)
        if workspace is None:
            raise HTTPException(status_code=404, detail="Workspace not found")
        workflow_key = str(workspace.get("default_workflow_key") or "")
        revision = job_db.get_active_workflow_revision(workspace_id, workflow_key)
        if revision is None:
            raise HTTPException(status_code=404, detail="No active workflow revision")
        # #211 M2: deprecated field backfill (see list route above).
        revision.setdefault("workflow_key", workflow_key)
        definition = workflow_definition_from_dict(json.loads(str(revision["definition_json"])))
        return ActiveWorkflowRevisionResponse(
            revision=WorkflowRevisionSummary.model_validate(revision),
            workflow=workflow_contracts.WorkflowDefinitionResponse.model_validate(
                workflow_definition_to_response_payload(definition)
            ),
            definition_yaml=definition_to_yaml(definition),
        )

    @router.get(
        "/workspaces/{workspace_id}/workflow-revisions/{revision_id}",
        response_model=WorkflowRevisionDetailResponse,
    )
    def get_workflow_revision_detail(
        workspace_id: str,
        revision_id: str,
    ) -> WorkflowRevisionDetailResponse:
        workspace = job_db.get_workspace(workspace_id)
        if workspace is None:
            raise HTTPException(status_code=404, detail="Workspace not found")
        workflow_key = str(workspace.get("default_workflow_key") or "")
        revision = job_db.get_workflow_revision(workspace_id, workflow_key, revision_id)
        if revision is None:
            raise HTTPException(status_code=404, detail="Workflow revision not found")
        # #211 M2: deprecated field backfill (see list route above).
        revision.setdefault("workflow_key", workflow_key)
        definition = workflow_definition_from_dict(json.loads(str(revision["definition_json"])))
        return WorkflowRevisionDetailResponse(
            revision=WorkflowRevisionSummary.model_validate(revision),
            workflow=workflow_contracts.WorkflowDefinitionResponse.model_validate(
                workflow_definition_to_response_payload(definition)
            ),
            definition_yaml=definition_to_yaml(definition),
        )

    router.include_router(create_workflow_draft_publish_router(job_db, settings))
    # Agent-initiated publish handshake (#416): pending read + confirm/cancel
    # (confirm replays the manual publish gates; reject_studio_agent_scope
    # inside the router keeps scoped tokens off all three).
    router.include_router(create_studio_publish_request_router(job_db, settings))
    router.include_router(create_workflow_draft_compare_router(job_db))
    router.include_router(create_workflow_draft_store_router(job_db))
    router.include_router(create_workflow_node_prompt_router(job_db))
    return router
