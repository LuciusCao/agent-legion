"""Studio-agent agent-definition tool endpoints (issue #633).

The workspace-scoped Agent surface for the authoring agent: read the
workspace's Agent definitions (latest versions, all fields), draft an Agent
definition, read the workspace's runtime → provider → models view aggregated
from online Workers, and read the per-runtime tool catalog. Read-only or
draft-only by design:

- Provider/model declarations are worker-owned (EXEC-RUNTIME-MODELS-001);
  this surface only reads the aggregation — there is no edit tool.
- The agent tool catalog is a code-defined static projection
  (EXEC-RUNTIME-CATALOG-001); the only editable tool surface is the
  ``tools`` selection inside an Agent definition draft.
- Publishing/archiving Agent definitions stays on the guarded human router
  (STUDIO-AGENT-001).
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
from server.app.routes.agent_runtimes_contracts import AgentRuntimesResponse
from server.app.routes.job_http import raise_job_http_error
from server.app.routes.studio_agent_tool_contracts import StudioAgentAgentVersionsResponse
from server.app.routes.workspace_runtime_models import WorkspaceRuntimeModelsResponse
from server.app.services.job_errors import JobServiceError
from server.app.services.studio_agent_catalog_reads import StudioAgentCatalogReads
from server.app.services.studio_agent_tools import StudioAgentToolsService
from server.app.services.versioned_entities import VersionedEntity
from server.app.settings import Settings


def _version_response(entity: VersionedEntity) -> AgentVersionResponse:
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


def _parse_agent_definition(payload: AgentDefinitionPayload) -> AgentDefinition:
    try:
        return AgentDefinition.model_validate(payload.model_dump())
    except ValidationError as exc:
        # ctx carries the raw exception objects — not JSON serializable.
        detail = [{k: v for k, v in error.items() if k != "ctx"} for error in exc.errors()]
        raise HTTPException(status_code=422, detail=detail) from exc


def create_studio_agent_catalog_read_tools_router(
    job_db: JobQueries, settings: Settings
) -> APIRouter:
    """Workspace-scoped Agent-definition catalog tools (issue #633).

    Mounted on the ``workspace_scoped`` sub-router inside
    ``studio_agent_tools.create_studio_agent_tools_router``: every endpoint
    requires a studio-agent scoped token AND honors the run token's
    workspace binding (STUDIO-AGENT-001).
    """
    router = APIRouter(
        dependencies=[Depends(require_studio_agent_scope), Depends(require_studio_agent_workspace)]
    )
    reads = StudioAgentCatalogReads(job_db)

    @router.get(
        "/studio-agent/tools/workspaces/{workspace_id}/agent-definitions",
        response_model=StudioAgentAgentVersionsResponse,
    )
    def list_agent_definitions(workspace_id: str) -> StudioAgentAgentVersionsResponse:
        """Latest version per Agent of the workspace (a pending draft beats
        the published row), with the full definition payload — the read side
        of the agent-authoring loop."""
        try:
            entities = reads.list_agent_definitions(workspace_id)
        except JobServiceError as exc:
            raise_job_http_error(exc)
        return StudioAgentAgentVersionsResponse(versions=[_version_response(e) for e in entities])

    @router.put(
        "/studio-agent/tools/workspaces/{workspace_id}/agent-definitions/{agent_id}/draft",
        response_model=AgentVersionResponse,
    )
    def save_agent_definition_draft(
        workspace_id: str,
        agent_id: str,
        payload: AgentDefinitionPayload,
        user: Annotated[dict[str, Any], Depends(require_studio_agent_scope)],
    ) -> AgentVersionResponse:
        """Draft-only write: a human publishes it in Studio (STUDIO-AGENT-001)."""
        definition = _parse_agent_definition(payload)
        try:
            entity = StudioAgentToolsService(job_db, settings).save_agent_definition_draft(
                workspace_id, agent_id, definition, str(user["id"])
            )
        except JobServiceError as exc:
            raise_job_http_error(exc)
        return _version_response(entity)

    @router.get(
        "/studio-agent/tools/workspaces/{workspace_id}/runtime-models",
        response_model=WorkspaceRuntimeModelsResponse,
    )
    def get_runtime_models(workspace_id: str) -> WorkspaceRuntimeModelsResponse:
        """``{runtime: {provider: [models]}}`` across the workspace's online
        Workers — read-only visibility (EXEC-RUNTIME-MODELS-001): workers own
        provider/model declarations; there is no tool to edit them."""
        try:
            models = reads.runtime_models(workspace_id)
        except JobServiceError as exc:
            raise_job_http_error(exc)
        return WorkspaceRuntimeModelsResponse(runtimes=models)

    @router.get(
        "/studio-agent/tools/workspaces/{workspace_id}/agent-runtimes",
        response_model=AgentRuntimesResponse,
        response_model_exclude_none=True,
    )
    def get_agent_runtimes(workspace_id: str) -> AgentRuntimesResponse:
        """Per-runtime agent tool catalog (tool names, tiers, activation).
        Static code-defined projection (EXEC-RUNTIME-CATALOG-001): agent
        "tools" are not runtime-editable — the editable surface is the
        ``tools`` selection inside Agent definition drafts."""
        del workspace_id  # catalog is global; the path keys it to the workspace
        return AgentRuntimesResponse(**reads.agent_runtimes())

    return router
