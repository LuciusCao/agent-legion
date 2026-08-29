"""Studio-agent tool surface (phase 3 chunk 3, decision §0.2).

Tool endpoints for the built-in Studio authoring agent: every endpoint
requires a studio-agent scoped token (full user/admin sessions are refused),
and the surface exposes only draft/validate/register-request operations plus
reads; effecting operations (publish/rollback/archive) stay on the
human-facing routers behind ``reject_studio_agent_scope`` (STUDIO-AGENT-001).
"""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import ValidationError

from server.app.agent_catalog import AgentDefinition
from server.app.auth.dependencies import (
    require_studio_agent_scope,
    require_studio_agent_workspace,
)
from server.app.jobs import JobQueries
from server.app.routes.agent_definition_contracts import (
    AgentDefinitionPayload,
    AgentVersionResponse,
)
from server.app.routes.job_http import raise_job_http_error, require_workflows_enabled
from server.app.routes.studio_agent_prompt_tools import create_studio_agent_prompt_tools_router
from server.app.routes.studio_agent_skill_tools import create_studio_agent_skill_tools_router
from server.app.routes.studio_agent_tool_contracts import (
    StudioAgentActiveWorkflowResponse,
    StudioAgentNodeCodeDraftRequest,
)
from server.app.routes.workflow_draft_compare_contracts import WorkflowDraftCompareResponse
from server.app.routes.workflow_node_code_contracts import (
    WorkflowNodeCodeResponse,
    WorkflowNodeCodeVersionResponse,
)
from server.app.routes.workflow_revisions_contracts import (
    WorkflowDraftRequest,
    WorkflowDraftValidationResponse,
)
from server.app.services.job_errors import JobServiceError
from server.app.services.studio_agent_tools import StudioAgentToolsService
from server.app.services.versioned_entities import VersionedEntity
from server.app.settings import Settings


def _parse_agent_definition(payload: AgentDefinitionPayload) -> AgentDefinition:
    try:
        return AgentDefinition.model_validate(payload.model_dump())
    except ValidationError as exc:
        # ctx carries the raw exception objects — not JSON serializable.
        detail = [{k: v for k, v in error.items() if k != "ctx"} for error in exc.errors()]
        raise HTTPException(status_code=422, detail=detail) from exc


def _agent_version_response(entity: VersionedEntity) -> AgentVersionResponse:
    return AgentVersionResponse(
        id=entity.id,
        agent_id=entity.entity_key,
        version=entity.version,
        status=entity.status,
        definition=entity.definition,
        definition_hash=entity.definition_hash,
        created_by=entity.created_by,
        created_at=entity.created_at,
        published_at=entity.published_at,
    )


def create_studio_agent_tools_router(job_db: JobQueries, settings: Settings) -> APIRouter:
    router = APIRouter(dependencies=[Depends(require_studio_agent_scope)])
    # Workspace-path endpoints additionally enforce the run token's workspace
    # binding (schema v45, STUDIO-AGENT-001); global endpoints stay on `router`.
    workspace_scoped = APIRouter(dependencies=[Depends(require_studio_agent_workspace)])

    def _service() -> StudioAgentToolsService:
        return StudioAgentToolsService(job_db, settings)

    @workspace_scoped.post(
        "/studio-agent/tools/workspaces/{workspace_id}/workflow/validate",
        response_model=WorkflowDraftValidationResponse,
    )
    def validate_workflow(
        workspace_id: str, payload: WorkflowDraftRequest
    ) -> WorkflowDraftValidationResponse:
        require_workflows_enabled(settings)
        errors = _service().validate_workflow(workspace_id, payload.definition_yaml)
        return WorkflowDraftValidationResponse(valid=not errors, errors=errors)

    @workspace_scoped.post(
        "/studio-agent/tools/workspaces/{workspace_id}/workflow/compare",
        response_model=WorkflowDraftCompareResponse,
    )
    def compare_workflow(
        workspace_id: str, payload: WorkflowDraftRequest
    ) -> WorkflowDraftCompareResponse:
        require_workflows_enabled(settings)
        try:
            result = _service().compare_workflow(workspace_id, payload.definition_yaml)
        except JobServiceError as exc:
            raise_job_http_error(exc)
        return WorkflowDraftCompareResponse.model_validate(result)

    @workspace_scoped.put(
        "/studio-agent/tools/workspaces/{workspace_id}/workflows/{workflow_key}"
        "/nodes/{node_key}/code/draft",
        response_model=WorkflowNodeCodeVersionResponse,
    )
    def save_node_code_draft(
        workspace_id: str,
        workflow_key: str,
        node_key: str,
        payload: StudioAgentNodeCodeDraftRequest,
        user: Annotated[dict[str, Any], Depends(require_studio_agent_scope)],
    ) -> WorkflowNodeCodeVersionResponse:
        require_workflows_enabled(settings)
        try:
            row = _service().save_node_code_draft(
                workspace_id,
                workflow_key,
                node_key,
                payload.code,
                payload.change_note,
                str(user["id"]),
                expected_capability=payload.expected_capability,
            )
        except JobServiceError as exc:
            raise_job_http_error(exc)
        return WorkflowNodeCodeVersionResponse(**row)

    @workspace_scoped.put(
        "/studio-agent/tools/workspaces/{workspace_id}/agent-definitions/{agent_id}/draft",
        response_model=AgentVersionResponse,
    )
    def save_agent_definition_draft(
        workspace_id: str,
        agent_id: str,
        payload: AgentDefinitionPayload,
        user: Annotated[dict[str, Any], Depends(require_studio_agent_scope)],
    ) -> AgentVersionResponse:
        require_workflows_enabled(settings)
        definition = _parse_agent_definition(payload)
        try:
            entity = _service().save_agent_definition_draft(
                workspace_id, agent_id, definition, str(user["id"])
            )
        except JobServiceError as exc:
            raise_job_http_error(exc)
        return _agent_version_response(entity)

    @workspace_scoped.get(
        "/studio-agent/tools/workspaces/{workspace_id}/workflow/active",
        response_model=StudioAgentActiveWorkflowResponse,
    )
    def get_active_revision(workspace_id: str) -> StudioAgentActiveWorkflowResponse:
        require_workflows_enabled(settings)
        try:
            payload = _service().get_active_revision(workspace_id)
        except JobServiceError as exc:
            raise_job_http_error(exc)
        return StudioAgentActiveWorkflowResponse.model_validate(payload)

    @workspace_scoped.get(
        "/studio-agent/tools/workspaces/{workspace_id}/workflows/{workflow_key}"
        "/nodes/{node_key}/code",
        response_model=WorkflowNodeCodeResponse,
    )
    def get_node_code_state(
        workspace_id: str, workflow_key: str, node_key: str
    ) -> WorkflowNodeCodeResponse:
        require_workflows_enabled(settings)
        try:
            state = _service().get_node_code_state(workspace_id, workflow_key, node_key)
        except JobServiceError as exc:
            raise_job_http_error(exc)
        return WorkflowNodeCodeResponse(**state)

    router.include_router(create_studio_agent_skill_tools_router(job_db, settings))
    workspace_scoped.include_router(create_studio_agent_prompt_tools_router(job_db, settings))
    router.include_router(workspace_scoped)
    return router
