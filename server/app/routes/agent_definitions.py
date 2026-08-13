from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import ValidationError

from server.app.agent_catalog import AgentDefinition
from server.app.auth.dependencies import reject_studio_agent_scope, require_user
from server.app.jobs import JobQueries
from server.app.routes.agent_definition_contracts import (
    AgentArchiveResponse,
    AgentCopyRequest,
    AgentCreateRequest,
    AgentDefinitionPayload,
    AgentDetailResponse,
    AgentListItem,
    AgentListResponse,
    AgentRollbackRequest,
    AgentVersionResponse,
    AgentVersionsResponse,
    AgentVersionSummary,
)
from server.app.routes.job_http import raise_job_http_error, require_workflows_enabled
from server.app.services.agent_service import AgentService
from server.app.services.job_errors import JobServiceError
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


def _parse_definition(payload: AgentDefinitionPayload) -> AgentDefinition:
    try:
        # AgentCreateRequest carries agent_id, which is not a definition field.
        return AgentDefinition.model_validate(
            payload.model_dump(include=set(AgentDefinitionPayload.model_fields))
        )
    except ValidationError as exc:
        # ctx carries the raw exception objects — not JSON serializable.
        detail = [{k: v for k, v in error.items() if k != "ctx"} for error in exc.errors()]
        raise HTTPException(status_code=422, detail=detail) from exc


def create_agent_definitions_router(job_db: JobQueries, settings: Settings) -> APIRouter:
    """DB-backed Agent definition catalog: draft → publish lifecycle (v26).

    Mounted through ``secured()``: workspace members manage Agent definitions.
    Execution configuration (provider/model/thinking) is deliberately absent —
    it resolves per node with workspace defaults, never on the definition.
    """
    router = APIRouter()

    def _service() -> AgentService:
        return AgentService(job_db.path)

    @router.get("/agent-definitions", response_model=AgentListResponse)
    def list_agent_definitions() -> AgentListResponse:
        require_workflows_enabled(settings)
        items: list[AgentListItem] = []
        for entity in _service().list_latest():
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
                )
            )
        return AgentListResponse(agents=items)

    @router.post("/agent-definitions", response_model=AgentVersionResponse)
    def create_agent_definition(
        request: AgentCreateRequest,
        user: Annotated[dict[str, Any], Depends(require_user)],
    ) -> AgentVersionResponse:
        require_workflows_enabled(settings)
        definition = _parse_definition(request)
        try:
            entity = _service().save_draft(request.agent_id, definition, f"user:{user['id']}")
        except JobServiceError as exc:
            raise_job_http_error(exc)
        return _version_response(entity)

    @router.get("/agent-definitions/{agent_id}", response_model=AgentDetailResponse)
    def get_agent_definition(agent_id: str) -> AgentDetailResponse:
        require_workflows_enabled(settings)
        service = _service()
        versions = service.list_versions(agent_id)
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
    def list_agent_definition_versions(agent_id: str) -> AgentVersionsResponse:
        require_workflows_enabled(settings)
        versions = _service().list_versions(agent_id)
        if not versions:
            raise HTTPException(status_code=404, detail=f"Unknown Agent: {agent_id}")
        return AgentVersionsResponse(versions=[_version_summary(v) for v in versions])

    @router.put("/agent-definitions/{agent_id}/draft", response_model=AgentVersionResponse)
    def save_agent_definition_draft(
        agent_id: str,
        request: AgentDefinitionPayload,
        user: Annotated[dict[str, Any], Depends(require_user)],
    ) -> AgentVersionResponse:
        require_workflows_enabled(settings)
        definition = _parse_definition(request)
        try:
            entity = _service().save_draft(agent_id, definition, f"user:{user['id']}")
        except JobServiceError as exc:
            raise_job_http_error(exc)
        return _version_response(entity)

    @router.post("/agent-definitions/{agent_id}/publish", response_model=AgentVersionResponse)
    def publish_agent_definition(
        agent_id: str, _guard: Annotated[None, Depends(reject_studio_agent_scope)] = None
    ) -> AgentVersionResponse:
        require_workflows_enabled(settings)
        try:
            entity = _service().publish(agent_id)
        except JobServiceError as exc:
            raise_job_http_error(exc)
        return _version_response(entity)

    @router.post("/agent-definitions/{agent_id}/rollback", response_model=AgentVersionResponse)
    def rollback_agent_definition(
        agent_id: str,
        request: AgentRollbackRequest,
        user: Annotated[dict[str, Any], Depends(require_user)],
        _guard: Annotated[None, Depends(reject_studio_agent_scope)] = None,
    ) -> AgentVersionResponse:
        require_workflows_enabled(settings)
        try:
            entity = _service().rollback(agent_id, request.version, f"user:{user['id']}")
        except JobServiceError as exc:
            raise_job_http_error(exc)
        return _version_response(entity)

    @router.post("/agent-definitions/{agent_id}/copy", response_model=AgentVersionResponse)
    def copy_agent_definition(
        agent_id: str,
        request: AgentCopyRequest,
        user: Annotated[dict[str, Any], Depends(require_user)],
    ) -> AgentVersionResponse:
        require_workflows_enabled(settings)
        try:
            entity = _service().copy(agent_id, request.new_agent_id, f"user:{user['id']}")
        except JobServiceError as exc:
            raise_job_http_error(exc)
        return _version_response(entity)

    @router.delete("/agent-definitions/{agent_id}", response_model=AgentArchiveResponse)
    def archive_agent_definition(
        agent_id: str, _guard: Annotated[None, Depends(reject_studio_agent_scope)] = None
    ) -> AgentArchiveResponse:
        require_workflows_enabled(settings)
        try:
            archived = _service().archive_all(agent_id)
        except JobServiceError as exc:
            raise_job_http_error(exc)
        return AgentArchiveResponse(archived=archived)

    return router
