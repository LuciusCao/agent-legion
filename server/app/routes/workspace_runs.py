from typing import Annotated

from fastapi import APIRouter, Query

from server.app.routes.job_contracts import WorkspaceDagResponse, WorkspaceRunsResponse
from server.app.routes.job_view_contracts import NodeRunResponse
from server.app.services.job_queries import JobQueryService

# #735 review P2 (cluster): same empty-string convention as the jobs list
# read surface — an optional filter's ``?param=`` form is a caller error
# (422); the query layer's `if val` must never turn it into "filter off".
_NonEmptyFilter = Annotated[str | None, Query(min_length=1)]


def create_workspace_runs_router(service: JobQueryService) -> APIRouter:
    router = APIRouter()

    @router.get("/workspaces/{workspace_id}/node-runs", response_model=WorkspaceRunsResponse)
    def list_workspace_runs(
        workspace_id: str,
        status: _NonEmptyFilter = None,
        node_key: _NonEmptyFilter = None,
        job_id: _NonEmptyFilter = None,
        skill: _NonEmptyFilter = None,
        limit: int = 100,
    ) -> WorkspaceRunsResponse:
        # #410 review: runs validate against NodeRunResponse now — the
        # service returns model-ready dicts (path-resolved node_runs rows).
        # #410 codex four-pass P1: the skill filter (schema v75) lets the
        # studio latest-run echo scope to the current binding — a rebound
        # node must not echo the previous skill's run version.
        return WorkspaceRunsResponse(
            runs=[
                NodeRunResponse.model_validate(run)
                for run in service.workspace_runs(
                    workspace_id, status, node_key, job_id, skill, limit
                )
            ]
        )

    @router.get("/workspaces/{workspace_id}/dag", response_model=WorkspaceDagResponse)
    def get_workspace_dag(workspace_id: str) -> WorkspaceDagResponse:
        return WorkspaceDagResponse(**service.workspace_dag(workspace_id))

    return router
