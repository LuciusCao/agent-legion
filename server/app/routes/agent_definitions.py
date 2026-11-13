from typing import Annotated

from fastapi import APIRouter, HTTPException, Query

from server.app.jobs import JobQueries
from server.app.routes.agent_definition_contracts import (
    AgentDetailResponse,
    AgentListItem,
    AgentListResponse,
    AgentVersionResponse,
    AgentVersionsResponse,
    AgentVersionSummary,
)
from server.app.services.agent_service import AgentService
from server.app.services.versioned_entities import VersionedEntity

# The catalog is workspace-scoped (schema v46): every endpoint takes the
# required workspace_id query parameter, which the router-level
# workspace-access dependency also uses for membership enforcement.
WorkspaceId = Annotated[str, Query()]


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


def _version_summary(entity: VersionedEntity) -> AgentVersionSummary:
    return AgentVersionSummary(
        id=entity.id,
        agent_id=entity.entity_key,
        version=entity.version,
        status=entity.status,
        definition_hash=entity.definition_hash,
        created_by=entity.created_by,
        created_at=entity.created_at,
        published_at=entity.published_at,
    )


def create_agent_definitions_router(job_db: JobQueries) -> APIRouter:
    """Read-only historical Agent definitions, bound to one workspace."""
    router = APIRouter()

    def _service(workspace_id: str) -> AgentService:
        return AgentService(job_db, workspace_id)

    @router.get("/agent-definitions", response_model=AgentListResponse)
    def list_agent_definitions(workspace_id: WorkspaceId) -> AgentListResponse:
        service = _service(workspace_id)
        # #906: reference checks must use the published capability (what
        # active revisions route to), not the latest draft's.
        published = {entity.entity_key: entity for entity in service.list_published()}
        items: list[AgentListItem] = []
        for entity in service.list_latest():
            routed = published.get(entity.entity_key)
            items.append(
                AgentListItem(
                    agent_id=entity.entity_key,
                    capability=str(entity.definition.get("capability", "")),
                    runtime=str(entity.definition.get("runtime", "")),
                    skill=str(entity.definition.get("skill", "")),
                    version=entity.version,
                    status=entity.status,
                    has_draft=entity.status == "draft",
                    published_at=entity.published_at,
                    published_capability=(
                        str(routed.definition.get("capability", "")) if routed else None
                    ),
                    published_version=routed.version if routed else None,
                )
            )
        return AgentListResponse(agents=items)

    @router.get("/agent-definitions/{agent_id}", response_model=AgentDetailResponse)
    def get_agent_definition(agent_id: str, workspace_id: WorkspaceId) -> AgentDetailResponse:
        versions = _service(workspace_id).list_versions(agent_id)
        if not versions:
            raise HTTPException(status_code=404, detail=f"Unknown Agent: {agent_id}")
        latest = versions[0]
        published = next((v for v in versions if v.status == "published"), None)
        return AgentDetailResponse(
            agent_id=agent_id,
            latest=_version_response(latest),
            published=_version_response(published) if published else None,
        )

    @router.get("/agent-definitions/{agent_id}/versions", response_model=AgentVersionsResponse)
    def list_agent_definition_versions(
        agent_id: str, workspace_id: WorkspaceId
    ) -> AgentVersionsResponse:
        versions = _service(workspace_id).list_versions(agent_id)
        if not versions:
            raise HTTPException(status_code=404, detail=f"Unknown Agent: {agent_id}")
        return AgentVersionsResponse(versions=[_version_summary(v) for v in versions])

    return router
