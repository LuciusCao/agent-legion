"""Studio-agent job observation tool surface (#329).

Read-only endpoints for the job-diagnosis agent loop. Same guard layout as
the authoring tool surface (studio_agent_tools.py): every endpoint requires a
studio-agent scoped token (full user sessions are refused), workspace-path
endpoints additionally enforce the run token's workspace binding
(require_studio_agent_workspace), and the session-bound context endpoint
authorizes inside the service (bound token → workspace match, mismatches 404).

There are deliberately no effecting endpoints here: agents only receive
``suggested_actions`` payloads; the human confirms in the UI and the host
session executes on the regular job routes (STUDIO-AGENT-001).
"""

from typing import Annotated, Any

from fastapi import APIRouter, Depends

from server.app.auth.dependencies import (
    require_studio_agent_scope,
    require_studio_agent_workspace,
)
from server.app.jobs import JobQueries
from server.app.routes.job_http import raise_job_http_error, require_workflows_enabled
from server.app.routes.studio_agent_job_tool_contracts import (
    StudioAgentArtifactResponse,
    StudioAgentJobCompareResponse,
    StudioAgentJobContextResponse,
    StudioAgentJobDetail,
    StudioAgentJobListResponse,
    StudioAgentJobLogsResponse,
)
from server.app.services.job_errors import JobServiceError
from server.app.services.studio_agent_job_tools import StudioAgentJobToolsService
from server.app.settings import Settings


def create_studio_agent_job_tools_router(
    job_db: JobQueries,
    settings: Settings,
    object_store: Any | None = None,
) -> APIRouter:
    router = APIRouter(dependencies=[Depends(require_studio_agent_scope)])
    workspace_scoped = APIRouter(dependencies=[Depends(require_studio_agent_workspace)])

    def _service() -> StudioAgentJobToolsService:
        return StudioAgentJobToolsService(job_db, settings, object_store=object_store)

    @router.get(
        "/studio-agent/tools/chat-sessions/{session_id}/job-context",
        response_model=StudioAgentJobContextResponse,
    )
    def get_job_context(
        session_id: str,
        job_id: str,
        user: Annotated[dict[str, Any], Depends(require_studio_agent_scope)],
        node_key: str | None = None,
    ) -> StudioAgentJobContextResponse:
        require_workflows_enabled(settings)
        try:
            context = _service().get_job_context(session_id, user, job_id, node_key)
        except JobServiceError as exc:
            raise_job_http_error(exc)
        return StudioAgentJobContextResponse.model_validate(context)

    @workspace_scoped.get(
        "/studio-agent/tools/workspaces/{workspace_id}/jobs",
        response_model=StudioAgentJobListResponse,
    )
    def list_jobs(
        workspace_id: str, status: str | None = None, limit: int = 20
    ) -> StudioAgentJobListResponse:
        require_workflows_enabled(settings)
        try:
            result = _service().list_jobs(workspace_id, status=status, limit=limit)
        except JobServiceError as exc:
            raise_job_http_error(exc)
        return StudioAgentJobListResponse.model_validate(result)

    # Registered BEFORE /jobs/{job_id} so the literal segment wins.
    @workspace_scoped.get(
        "/studio-agent/tools/workspaces/{workspace_id}/jobs/compare",
        response_model=StudioAgentJobCompareResponse,
    )
    def compare_jobs(
        workspace_id: str, job_id_a: str, job_id_b: str
    ) -> StudioAgentJobCompareResponse:
        require_workflows_enabled(settings)
        try:
            result = _service().compare_jobs(workspace_id, job_id_a, job_id_b)
        except JobServiceError as exc:
            raise_job_http_error(exc)
        return StudioAgentJobCompareResponse.model_validate(result)

    @workspace_scoped.get(
        "/studio-agent/tools/workspaces/{workspace_id}/jobs/{job_id}",
        response_model=StudioAgentJobDetail,
    )
    def get_job_detail(workspace_id: str, job_id: str) -> StudioAgentJobDetail:
        require_workflows_enabled(settings)
        try:
            detail = _service().get_job_detail(workspace_id, job_id)
        except JobServiceError as exc:
            raise_job_http_error(exc)
        return StudioAgentJobDetail.model_validate(detail)

    @workspace_scoped.get(
        "/studio-agent/tools/workspaces/{workspace_id}/jobs/{job_id}/logs",
        response_model=StudioAgentJobLogsResponse,
    )
    def get_node_logs(
        workspace_id: str,
        job_id: str,
        node_key: str | None = None,
        run_id: int | None = None,
    ) -> StudioAgentJobLogsResponse:
        require_workflows_enabled(settings)
        try:
            logs = _service().get_node_logs(workspace_id, job_id, node_key, run_id)
        except JobServiceError as exc:
            raise_job_http_error(exc)
        return StudioAgentJobLogsResponse.model_validate(logs)

    @workspace_scoped.get(
        "/studio-agent/tools/workspaces/{workspace_id}/jobs/{job_id}/artifacts/{artifact_name}",
        response_model=StudioAgentArtifactResponse,
    )
    def read_artifact(
        workspace_id: str, job_id: str, artifact_name: str
    ) -> StudioAgentArtifactResponse:
        require_workflows_enabled(settings)
        try:
            artifact = _service().read_artifact(workspace_id, job_id, artifact_name)
        except JobServiceError as exc:
            raise_job_http_error(exc)
        return StudioAgentArtifactResponse.model_validate(artifact)

    router.include_router(workspace_scoped)
    return router
