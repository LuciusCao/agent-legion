"""Workflow runtime channel routes (M47).

Mounted under the studio_secured() surface via create_workflow_revisions_router
(require_workspace_access + require_studio_authoring), alongside
workflow-drafts/* — the Studio separation: structural edits go through the
draft channel (compare → publish → new revision); runtime settings
(provider/model/thinking/prompt/prompt_mode/tools/requires_labels + the
top-level execution defaults of the same family) save directly here, taking
effect in place on the active revision with an appended immutable config
version (audit + rollback source). The PATCH/POST writes mount
reject_studio_agent_scope (STUDIO-AGENT-001), same convention as the draft
PUT: a scoped run token has no business mutating runtime config; the GET
stays readable to it (it authenticates as the initiating user).
"""

import json
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException

from server.app.auth.dependencies import reject_studio_agent_scope
from server.app.auth.workspace_access import require_workspace_access
from server.app.jobs import JobQueries
from server.app.routes.workflow_revisions_contracts import WorkflowRevisionSummary
from server.app.routes.workflow_runtime_contracts import (
    RuntimeConfigVersionSummary,
    WorkflowRuntimeRollbackRequest,
    WorkflowRuntimeSaveRequest,
    WorkflowRuntimeSaveResponse,
    WorkflowRuntimeVersionsResponse,
)
from server.app.services.runtime_config import (
    rollback_workflow_runtime_config,
    save_workflow_runtime_config,
)
from server.app.services.workflow_draft_key import require_draft_workflow_key_match
from server.app.services.workflow_drafts import (
    validate_workflow_definition,
    workflow_definition_from_yaml_string,
)

_WRITE_GUARD = [Depends(reject_studio_agent_scope)]


def _version_summary(row: dict[str, Any]) -> RuntimeConfigVersionSummary:
    return RuntimeConfigVersionSummary(
        version=int(row["version"]),
        revision_id=str(row["revision_id"]),
        changed_keys=json.loads(str(row["changed_keys_json"])),
        created_by=str(row["created_by"]),
        created_at=str(row["created_at"]),
    )


def _save_response(result: dict[str, Any]) -> WorkflowRuntimeSaveResponse:
    version_row = result["runtime_version"]
    return WorkflowRuntimeSaveResponse(
        revision=WorkflowRevisionSummary.model_validate(result["revision"]),
        runtime_version=_version_summary(version_row) if version_row is not None else None,
    )


def create_workflow_runtime_router(job_db: JobQueries) -> APIRouter:
    router = APIRouter()

    @router.patch(
        "/workspaces/{workspace_id}/workflow-runtime",
        response_model=WorkflowRuntimeSaveResponse,
        dependencies=_WRITE_GUARD,
    )
    def save_runtime(
        workspace_id: str,
        request: WorkflowRuntimeSaveRequest,
        user: Annotated[dict[str, Any], Depends(require_workspace_access)],
    ) -> WorkflowRuntimeSaveResponse:
        require_draft_workflow_key_match(job_db, workspace_id, request.definition_yaml)
        errors = validate_workflow_definition(request.definition_yaml)
        if errors:
            raise HTTPException(status_code=422, detail=errors)
        result = save_workflow_runtime_config(
            job_db,
            workspace_id,
            workflow_definition_from_yaml_string(request.definition_yaml),
            created_by=f"user:{user['id']}",
        )
        return _save_response(result)

    @router.get(
        "/workspaces/{workspace_id}/workflow-runtime/versions",
        response_model=WorkflowRuntimeVersionsResponse,
    )
    def list_runtime_versions(workspace_id: str) -> WorkflowRuntimeVersionsResponse:
        workspace = job_db.get_workspace(workspace_id)
        if workspace is None:
            return WorkflowRuntimeVersionsResponse(versions=[])
        return WorkflowRuntimeVersionsResponse(
            versions=[
                _version_summary(row) for row in job_db.list_runtime_config_versions(workspace_id)
            ]
        )

    @router.post(
        "/workspaces/{workspace_id}/workflow-runtime/rollback",
        response_model=WorkflowRuntimeSaveResponse,
        dependencies=_WRITE_GUARD,
    )
    def rollback_runtime(
        workspace_id: str,
        request: WorkflowRuntimeRollbackRequest,
        user: Annotated[dict[str, Any], Depends(require_workspace_access)],
    ) -> WorkflowRuntimeSaveResponse:
        result = rollback_workflow_runtime_config(
            job_db, workspace_id, request.version, created_by=f"user:{user['id']}"
        )
        return _save_response(result)

    return router
