import logging
from typing import Annotated, Any, Never

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import ValidationError

from server.app.auth.dependencies import reject_studio_agent_scope, require_user
from server.app.executors.executor_registry_factory import reload_published_executors
from server.app.executors.kinds import ExecutorKindError
from server.app.jobs import JobQueries
from server.app.routes.executor_definition_contracts import (
    ExecutorArchiveResponse,
    ExecutorCopyRequest,
    ExecutorCreateRequest,
    ExecutorDefinitionPayload,
    ExecutorDetailResponse,
    ExecutorListItem,
    ExecutorListResponse,
    ExecutorRollbackRequest,
    ExecutorVersionResponse,
    ExecutorVersionsResponse,
    ExecutorVersionSummary,
)
from server.app.routes.job_http import raise_job_http_error, require_workflows_enabled
from server.app.services.executor_definition_service import ExecutorDefinitionService
from server.app.services.job_errors import JobServiceError
from server.app.services.versioned_entities import VersionedEntity
from server.app.settings import Settings

logger = logging.getLogger(__name__)


def _version_response(entity: VersionedEntity) -> ExecutorVersionResponse:
    return ExecutorVersionResponse(
        id=entity.id,
        executor_id=entity.entity_key,
        version=entity.version,
        status=entity.status,
        definition=entity.definition,
        definition_hash=entity.definition_hash,
        created_by=entity.created_by,
        created_at=entity.created_at,
        published_at=entity.published_at,
    )


def _version_summary(entity: VersionedEntity) -> ExecutorVersionSummary:
    return ExecutorVersionSummary(
        id=entity.id,
        executor_id=entity.entity_key,
        version=entity.version,
        status=entity.status,
        definition_hash=entity.definition_hash,
        created_by=entity.created_by,
        created_at=entity.created_at,
        published_at=entity.published_at,
    )


def _raise_definition_http_error(exc: Exception) -> Never:
    """Map definition validation failures (service full parse) to HTTP errors."""
    if isinstance(exc, ValidationError):
        # ctx carries the raw exception objects — not JSON serializable.
        detail = [{k: v for k, v in error.items() if k != "ctx"} for error in exc.errors()]
        raise HTTPException(status_code=422, detail=detail) from exc
    if isinstance(exc, ExecutorKindError):
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if isinstance(exc, JobServiceError):
        raise_job_http_error(exc)
    raise exc


def create_executor_definitions_router(job_db: JobQueries, settings: Settings) -> APIRouter:
    """DB-backed executor definition catalog: draft → publish lifecycle (v30).

    Mounted through ``secured()``: workspace members manage executor
    definitions. Publish/rollback/archive hot-reload the runtime executor
    registry (``reload_published_executors``), so new definitions participate
    in scheduling without a service restart.
    """
    router = APIRouter()

    def _service() -> ExecutorDefinitionService:
        return ExecutorDefinitionService(job_db.path, settings.root_dir)

    def _reload_registry_best_effort(request: Request) -> None:
        # The publish/rollback/archive write is already committed when this
        # runs; a failed registry rebuild must not 500 the committed write.
        # The worker's poll loop reconciles the catalog periodically
        # (workflow_worker.catalog_reconcile), so the registry self-heals.
        try:
            reload_published_executors(settings, request.app.state.executor_registry)
        except Exception:
            logger.exception("executor registry hot reload failed for committed write")

    @router.get("/executor-definitions", response_model=ExecutorListResponse)
    def list_executor_definitions() -> ExecutorListResponse:
        require_workflows_enabled(settings)
        items: list[ExecutorListItem] = []
        for entity in _service().list_latest():
            capabilities = entity.definition.get("capabilities", {})
            items.append(
                ExecutorListItem(
                    executor_id=entity.entity_key,
                    kind=str(entity.definition.get("kind", "")),
                    global_capacity=int(entity.definition.get("global_capacity", 0)),
                    capabilities=sorted(capabilities) if isinstance(capabilities, dict) else [],
                    version=entity.version,
                    status=entity.status,
                    has_draft=entity.status == "draft",
                    published_at=entity.published_at,
                )
            )
        return ExecutorListResponse(executors=items)

    @router.post("/executor-definitions", response_model=ExecutorVersionResponse)
    def create_executor_definition(
        request: ExecutorCreateRequest,
        user: Annotated[dict[str, Any], Depends(require_user)],
    ) -> ExecutorVersionResponse:
        require_workflows_enabled(settings)
        definition = request.model_dump(include=set(ExecutorDefinitionPayload.model_fields))
        try:
            entity = _service().save_draft(request.executor_id, definition, f"user:{user['id']}")
        except (ValidationError, ExecutorKindError, JobServiceError) as exc:
            _raise_definition_http_error(exc)
        return _version_response(entity)

    @router.get("/executor-definitions/{executor_id}", response_model=ExecutorDetailResponse)
    def get_executor_definition(executor_id: str) -> ExecutorDetailResponse:
        require_workflows_enabled(settings)
        versions = _service().list_versions(executor_id)
        if not versions:
            raise HTTPException(status_code=404, detail=f"Unknown executor: {executor_id}")
        latest = versions[0]
        published = next((v for v in versions if v.status == "published"), None)
        return ExecutorDetailResponse(
            executor_id=executor_id,
            latest=_version_response(latest),
            published=_version_response(published) if published else None,
        )

    @router.get(
        "/executor-definitions/{executor_id}/versions",
        response_model=ExecutorVersionsResponse,
    )
    def list_executor_definition_versions(executor_id: str) -> ExecutorVersionsResponse:
        require_workflows_enabled(settings)
        versions = _service().list_versions(executor_id)
        if not versions:
            raise HTTPException(status_code=404, detail=f"Unknown executor: {executor_id}")
        return ExecutorVersionsResponse(versions=[_version_summary(v) for v in versions])

    @router.put("/executor-definitions/{executor_id}/draft", response_model=ExecutorVersionResponse)
    def save_executor_definition_draft(
        executor_id: str,
        request: ExecutorDefinitionPayload,
        user: Annotated[dict[str, Any], Depends(require_user)],
    ) -> ExecutorVersionResponse:
        require_workflows_enabled(settings)
        try:
            entity = _service().save_draft(executor_id, request.model_dump(), f"user:{user['id']}")
        except (ValidationError, ExecutorKindError, JobServiceError) as exc:
            _raise_definition_http_error(exc)
        return _version_response(entity)

    @router.post(
        "/executor-definitions/{executor_id}/publish",
        response_model=ExecutorVersionResponse,
        dependencies=[Depends(reject_studio_agent_scope)],
    )
    def publish_executor_definition(request: Request, executor_id: str) -> ExecutorVersionResponse:
        require_workflows_enabled(settings)
        try:
            entity = _service().publish(executor_id)
        except (ValidationError, ExecutorKindError, JobServiceError) as exc:
            _raise_definition_http_error(exc)
        _reload_registry_best_effort(request)
        return _version_response(entity)

    @router.post(
        "/executor-definitions/{executor_id}/rollback",
        response_model=ExecutorVersionResponse,
        dependencies=[Depends(reject_studio_agent_scope)],
    )
    def rollback_executor_definition(
        request: Request,
        executor_id: str,
        request_body: ExecutorRollbackRequest,
        user: Annotated[dict[str, Any], Depends(require_user)],
    ) -> ExecutorVersionResponse:
        require_workflows_enabled(settings)
        try:
            entity = _service().rollback(executor_id, request_body.version, f"user:{user['id']}")
        except (ValidationError, ExecutorKindError, JobServiceError) as exc:
            _raise_definition_http_error(exc)
        _reload_registry_best_effort(request)
        return _version_response(entity)

    @router.post("/executor-definitions/{executor_id}/copy", response_model=ExecutorVersionResponse)
    def copy_executor_definition(
        executor_id: str,
        request: ExecutorCopyRequest,
        user: Annotated[dict[str, Any], Depends(require_user)],
    ) -> ExecutorVersionResponse:
        require_workflows_enabled(settings)
        try:
            entity = _service().copy(executor_id, request.new_executor_id, f"user:{user['id']}")
        except (ValidationError, ExecutorKindError, JobServiceError) as exc:
            _raise_definition_http_error(exc)
        return _version_response(entity)

    @router.delete(
        "/executor-definitions/{executor_id}",
        response_model=ExecutorArchiveResponse,
        dependencies=[Depends(reject_studio_agent_scope)],
    )
    def archive_executor_definition(request: Request, executor_id: str) -> ExecutorArchiveResponse:
        require_workflows_enabled(settings)
        try:
            archived = _service().archive_all(executor_id)
        except JobServiceError as exc:
            raise_job_http_error(exc)
        _reload_registry_best_effort(request)
        return ExecutorArchiveResponse(archived=archived)

    return router
