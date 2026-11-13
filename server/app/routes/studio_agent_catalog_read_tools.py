"""Studio-agent agent-definition read tool endpoints (issue #633).

The workspace-scoped Agent surface for the authoring agent reads historical
Agent definitions (latest versions, all fields), the runtime → provider →
models view aggregated from online Workers, and the per-runtime tool catalog:

- Provider/model declarations are worker-owned (EXEC-RUNTIME-MODELS-001);
  this surface only reads the aggregation — there is no edit tool.
- The agent tool catalog is a code-defined static projection
  (EXEC-RUNTIME-CATALOG-001); node ``tools`` selections are edited in the
  workflow draft through ``save_workflow_draft``.
- Publishing/archiving historical Agent definitions stays on the guarded
  human router (STUDIO-AGENT-001).
"""

from fastapi import APIRouter, Depends

from server.app.auth.dependencies import (
    require_studio_agent_scope,
    require_studio_agent_workspace,
)
from server.app.jobs import JobQueries
from server.app.routes.agent_definition_contracts import AgentVersionResponse
from server.app.routes.agent_runtimes_contracts import AgentRuntimesResponse
from server.app.routes.studio_agent_tool_contracts import StudioAgentAgentVersionsResponse
from server.app.routes.workspace_runtime_models import WorkspaceRuntimeModelsResponse
from server.app.services.studio_agent_catalog_reads import StudioAgentCatalogReads
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


def create_studio_agent_catalog_read_tools_router(
    job_db: JobQueries, settings: Settings
) -> APIRouter:
    """Workspace-scoped Agent-definition catalog read tools (issue #633).

    Mounted on the ``workspace_scoped`` sub-router inside
    ``studio_agent_tools.create_studio_agent_tools_router``: every endpoint
    requires a studio-agent scoped token AND honors the run token's
    workspace binding (STUDIO-AGENT-001).
    """
    del settings  # the read-only surface has no write-service dependencies
    router = APIRouter(
        dependencies=[Depends(require_studio_agent_scope), Depends(require_studio_agent_workspace)]
    )
    reads = StudioAgentCatalogReads(job_db)

    @router.get(
        "/studio-agent/tools/workspaces/{workspace_id}/agent-definitions",
        response_model=StudioAgentAgentVersionsResponse,
    )
    def list_agent_definitions(workspace_id: str) -> StudioAgentAgentVersionsResponse:
        """Latest historical version per Agent of the workspace (a pending
        draft beats the published row), with the full definition payload."""
        entities = reads.list_agent_definitions(workspace_id)
        return StudioAgentAgentVersionsResponse(versions=[_version_response(e) for e in entities])

    @router.get(
        "/studio-agent/tools/workspaces/{workspace_id}/runtime-models",
        response_model=WorkspaceRuntimeModelsResponse,
    )
    def get_runtime_models(workspace_id: str) -> WorkspaceRuntimeModelsResponse:
        """``{runtime: {provider: [models]}}`` across the workspace's online
        Workers — read-only visibility (EXEC-RUNTIME-MODELS-001): workers own
        provider/model declarations; there is no tool to edit them."""
        models = reads.runtime_models(workspace_id)
        return WorkspaceRuntimeModelsResponse(runtimes=models)

    @router.get(
        "/studio-agent/tools/workspaces/{workspace_id}/agent-runtimes",
        response_model=AgentRuntimesResponse,
        response_model_exclude_none=True,
    )
    def get_agent_runtimes(workspace_id: str) -> AgentRuntimesResponse:
        """Per-runtime agent tool catalog (tool names, tiers, activation).
        Static code-defined projection (EXEC-RUNTIME-CATALOG-001): runtime
        catalogs are not editable; node ``tools`` selections live in the
        workflow draft."""
        del workspace_id  # catalog is global; the path keys it to the workspace
        return AgentRuntimesResponse(**reads.agent_runtimes())

    return router
