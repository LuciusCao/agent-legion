import json
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException

from server.app.auth.dependencies import reject_studio_agent_scope, require_user
from server.app.jobs import JobQueries
from server.app.routes.job_http import raise_job_http_error
from server.app.routes.workflow_node_code_contracts import (
    WorkflowNodeCodeArchiveResponse,
    WorkflowNodeCodeDraftRequest,
    WorkflowNodeCodeResponse,
    WorkflowNodeCodeRollbackRequest,
    WorkflowNodeCodeTemplateResponse,
    WorkflowNodeCodeVersionResponse,
    WorkflowNodeCodeVersionsResponse,
    WorkflowNodeCodeVersionSummary,
)
from server.app.services.job_errors import JobServiceError
from server.app.services.node_code_template import NODE_CODE_TEMPLATE
from server.app.services.node_codes import NodeCodeService
from server.app.settings import Settings
from server.app.workflows.definition import workflow_definition_from_dict
from server.app.workflows.start_node import START_NODE_TYPE

# #211 Phase 2: the {workflow_key} URL segment is a deprecated alias of the
# workspace id (equal since schema v62). Each endpoint keeps its historical
# path (deprecated) alongside the segment-free one; handlers fall back to the
# path workspace_id when the segment is absent.
_DEPRECATED_PATH = (
    "Deprecated path: workflows/{workflow_key} is the workspace id (equal since schema v62); "
    "use /workspaces/{id}/nodes/... — removal is tracked in #211."
)
_EDIT_GUARD = [Depends(reject_studio_agent_scope)]


def create_workflow_node_codes_router(job_db: JobQueries, settings: Settings) -> APIRouter:
    """Workspace-scoped custom node code editing and publish flow (EXEC-CODE-002).

    Mounted through ``secured()``: workspace members (viewer read, editor
    write) reach these endpoints — custom code is user data, not a repo file.
    """
    router = APIRouter()

    def _service() -> NodeCodeService:
        return NodeCodeService(job_db, settings.executor_runtime.workflows.custom_nodes_enabled)

    def _resolve_key(workspace_id: str, workflow_key: str | None) -> str:
        """Codex P2 on #299: the deprecated segment (bound as a query param on
        the segment-free path) must not steer the entity key away from the
        path workspace id — only its equal value is accepted (v62 invariant).
        """
        if workflow_key not in (None, workspace_id):
            raise HTTPException(
                status_code=400,
                detail="workflow_key must equal the workspace id (schema v62)",
            )
        return workspace_id

    def _reject_start_node(workspace_id: str, workflow_key: str, node_key: str) -> None:
        """Start nodes never execute: there is no code to edit (404).

        Draft-only nodes are allowed through: a workspace with no active
        revision yet (or a node the next publish will introduce) can draft
        node code before the revision exists. The start check is best-effort —
        without a revision the loader's synthetic start cannot be told apart
        from a draft node, and a code draft keyed to a would-be start node is
        harmless dead data (start nodes never execute).
        """
        revision = job_db.get_active_workflow_revision(workspace_id, workflow_key)
        if revision is None:
            return
        definition = workflow_definition_from_dict(json.loads(str(revision["definition_json"])))
        node = definition.nodes.get(node_key)
        if node is not None and node.node_type == START_NODE_TYPE:
            raise HTTPException(status_code=404, detail=f"Unknown workflow node: {node_key}")

    @router.get(
        "/workspaces/{workspace_id}/nodes/{node_key}/code",
        response_model=WorkflowNodeCodeResponse,
    )
    @router.get(
        "/workspaces/{workspace_id}/workflows/{workflow_key}/nodes/{node_key}/code",
        response_model=WorkflowNodeCodeResponse,
        deprecated=True,
        description=_DEPRECATED_PATH,
    )
    def get_node_code(
        workspace_id: str, node_key: str, workflow_key: str | None = None
    ) -> WorkflowNodeCodeResponse:
        key = _resolve_key(workspace_id, workflow_key)
        _reject_start_node(workspace_id, key, node_key)
        try:
            versions = _service().list_versions(workspace_id, key, node_key)
        except JobServiceError as exc:
            raise_job_http_error(exc)
        published = next((row for row in versions if row["status"] == "published"), None)
        # list_versions is version-descending: the first draft is the current one.
        draft = next((row for row in versions if row["status"] == "draft"), None)
        has_draft = draft is not None
        draft_code = str(draft["code"]) if draft is not None else None
        draft_version = int(draft["version"]) if draft is not None else None

        def _response(**kwargs: Any) -> WorkflowNodeCodeResponse:
            return WorkflowNodeCodeResponse(
                has_draft=has_draft, draft_code=draft_code, draft_version=draft_version, **kwargs
            )

        if published is not None:
            if published["created_by"] == "system":
                return _response(origin="builtin", code=str(published["code"]))
            return _response(
                origin="custom", code=str(published["code"]), version=int(published["version"])
            )
        # No workspace-published version: start from the SDK template.
        return _response(origin="none", code="")

    @router.put(
        "/workspaces/{workspace_id}/nodes/{node_key}/code",
        response_model=WorkflowNodeCodeVersionResponse,
    )
    @router.put(
        "/workspaces/{workspace_id}/workflows/{workflow_key}/nodes/{node_key}/code",
        response_model=WorkflowNodeCodeVersionResponse,
        deprecated=True,
        description=_DEPRECATED_PATH,
    )
    def save_node_code_draft(
        workspace_id: str,
        node_key: str,
        request: WorkflowNodeCodeDraftRequest,
        user: Annotated[dict[str, Any], Depends(require_user)],
        workflow_key: str | None = None,
    ) -> WorkflowNodeCodeVersionResponse:
        key = _resolve_key(workspace_id, workflow_key)
        _reject_start_node(workspace_id, key, node_key)
        try:
            row = _service().save_draft(
                workspace_id,
                key,
                node_key,
                request.code,
                f"user:{user['id']}",
                request.change_note,
            )
        except JobServiceError as exc:
            raise_job_http_error(exc)
        return WorkflowNodeCodeVersionResponse(**row)

    @router.post(
        "/workspaces/{workspace_id}/nodes/{node_key}/code/publish",
        response_model=WorkflowNodeCodeVersionResponse,
        dependencies=_EDIT_GUARD,
    )
    @router.post(
        "/workspaces/{workspace_id}/workflows/{workflow_key}/nodes/{node_key}/code/publish",
        response_model=WorkflowNodeCodeVersionResponse,
        deprecated=True,
        description=_DEPRECATED_PATH,
        dependencies=_EDIT_GUARD,
    )
    def publish_node_code(
        workspace_id: str, node_key: str, workflow_key: str | None = None
    ) -> WorkflowNodeCodeVersionResponse:
        key = _resolve_key(workspace_id, workflow_key)
        _reject_start_node(workspace_id, key, node_key)
        try:
            row = _service().publish(workspace_id, key, node_key)
        except JobServiceError as exc:
            raise_job_http_error(exc)
        return WorkflowNodeCodeVersionResponse(**row)

    @router.get(
        "/workspaces/{workspace_id}/nodes/{node_key}/code/versions",
        response_model=WorkflowNodeCodeVersionsResponse,
    )
    @router.get(
        "/workspaces/{workspace_id}/workflows/{workflow_key}/nodes/{node_key}/code/versions",
        response_model=WorkflowNodeCodeVersionsResponse,
        deprecated=True,
        description=_DEPRECATED_PATH,
    )
    def list_node_code_versions(
        workspace_id: str, node_key: str, workflow_key: str | None = None
    ) -> WorkflowNodeCodeVersionsResponse:
        key = _resolve_key(workspace_id, workflow_key)
        _reject_start_node(workspace_id, key, node_key)
        try:
            rows = _service().list_versions(workspace_id, key, node_key)
        except JobServiceError as exc:
            raise_job_http_error(exc)
        return WorkflowNodeCodeVersionsResponse(
            versions=[WorkflowNodeCodeVersionSummary(**row) for row in rows]
        )

    @router.get(
        "/workspaces/{workspace_id}/nodes/{node_key}/code/versions/{version}",
        response_model=WorkflowNodeCodeVersionResponse,
    )
    @router.get(
        "/workspaces/{workspace_id}/workflows/{workflow_key}/nodes/{node_key}"
        "/code/versions/{version}",
        response_model=WorkflowNodeCodeVersionResponse,
        deprecated=True,
        description=_DEPRECATED_PATH,
    )
    def get_node_code_version(
        workspace_id: str, node_key: str, version: int, workflow_key: str | None = None
    ) -> WorkflowNodeCodeVersionResponse:
        key = _resolve_key(workspace_id, workflow_key)
        _reject_start_node(workspace_id, key, node_key)
        try:
            row = _service().get_code_by_version(workspace_id, key, node_key, version)
        except JobServiceError as exc:
            raise_job_http_error(exc)
        if row is None:
            raise HTTPException(status_code=404, detail=f"No node code version {version}")
        return WorkflowNodeCodeVersionResponse(**row)

    @router.post(
        "/workspaces/{workspace_id}/nodes/{node_key}/code/rollback",
        response_model=WorkflowNodeCodeVersionResponse,
        dependencies=_EDIT_GUARD,
    )
    @router.post(
        "/workspaces/{workspace_id}/workflows/{workflow_key}/nodes/{node_key}/code/rollback",
        response_model=WorkflowNodeCodeVersionResponse,
        deprecated=True,
        description=_DEPRECATED_PATH,
        dependencies=_EDIT_GUARD,
    )
    def rollback_node_code(
        workspace_id: str,
        node_key: str,
        request: WorkflowNodeCodeRollbackRequest,
        user: Annotated[dict[str, Any], Depends(require_user)],
        workflow_key: str | None = None,
    ) -> WorkflowNodeCodeVersionResponse:
        key = _resolve_key(workspace_id, workflow_key)
        _reject_start_node(workspace_id, key, node_key)
        try:
            row = _service().rollback(
                workspace_id,
                key,
                node_key,
                request.version,
                f"user:{user['id']}",
            )
        except JobServiceError as exc:
            raise_job_http_error(exc)
        return WorkflowNodeCodeVersionResponse(**row)

    @router.delete(
        "/workspaces/{workspace_id}/nodes/{node_key}/code",
        response_model=WorkflowNodeCodeArchiveResponse,
        dependencies=_EDIT_GUARD,
    )
    @router.delete(
        "/workspaces/{workspace_id}/workflows/{workflow_key}/nodes/{node_key}/code",
        response_model=WorkflowNodeCodeArchiveResponse,
        deprecated=True,
        description=_DEPRECATED_PATH,
        dependencies=_EDIT_GUARD,
    )
    def archive_node_code(
        workspace_id: str, node_key: str, workflow_key: str | None = None
    ) -> WorkflowNodeCodeArchiveResponse:
        key = _resolve_key(workspace_id, workflow_key)
        _reject_start_node(workspace_id, key, node_key)
        try:
            archived = _service().archive_all(workspace_id, key, node_key)
        except JobServiceError as exc:
            raise_job_http_error(exc)
        return WorkflowNodeCodeArchiveResponse(archived=archived)

    @router.get(
        "/workflow-node-code-template",
        response_model=WorkflowNodeCodeTemplateResponse,
    )
    def get_node_code_template() -> WorkflowNodeCodeTemplateResponse:
        return WorkflowNodeCodeTemplateResponse(code=NODE_CODE_TEMPLATE)

    return router
