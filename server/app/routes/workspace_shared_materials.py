"""User-facing read-only shared-materials endpoints (issue #643).

``GET /api/workspaces/{id}/skills-shared`` mirrors the studio-agent read for
full user sessions (``require_workspace_access`` via ``secured()``), minus
the inline file contents — the UI fetches single files from
``GET .../skills-shared/file?path=...`` on demand — and plus a per-skill
drift status (``skill_shared_view``) telling whether each mapped skill
repo's HEAD copy still matches the shared source.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from server.app.auth.dependencies import reject_studio_agent_scope
from server.app.jobs import JobQueries
from server.app.routes.job_http import raise_job_http_error
from server.app.routes.workspace_shared_materials_contracts import (
    SharedMaterialFileContent,
    SharedMaterialFileEntry,
    SharedMaterialMapping,
    SharedMaterialSkillDrift,
    SharedMaterialsMapView,
    WorkspaceSharedMaterialsResponse,
)
from server.app.services.job_errors import JobServiceError, NotFoundError
from server.app.services.skill_repo_edit import SkillEditValidationError
from server.app.services.skill_shared_view import (
    get_shared_materials_view,
    read_shared_file_content,
)
from server.app.settings import Settings


def create_workspace_shared_materials_router(job_db: JobQueries, settings: Settings) -> APIRouter:
    del settings  # the shared dir resolves from the skills root (HOME)

    router = APIRouter(
        # Scoped identities (workspace-bound run tokens inherit the
        # initiator's role — admin passes require_workspace_access on ANY
        # workspace) must use their own studio-agent tool endpoints, never
        # this user-session surface (codex P1 on #674; same guard class as
        # the user-facing POST propagate).
        dependencies=[Depends(reject_studio_agent_scope)]
    )

    def _require_workspace(workspace_id: str) -> None:
        if job_db.get_workspace(workspace_id) is None:
            raise_job_http_error(NotFoundError("Workspace not found"))

    @router.get(
        "/workspaces/{workspace_id}/skills-shared",
        response_model=WorkspaceSharedMaterialsResponse,
    )
    def get_shared_materials(workspace_id: str) -> WorkspaceSharedMaterialsResponse:
        _require_workspace(workspace_id)
        try:
            view = get_shared_materials_view(workspace_id)
        except SkillEditValidationError as exc:
            raise_job_http_error(exc)
        materials = view.shared_map.materials if view.shared_map is not None else ()
        drift_by_source = {entry.source: entry.skills for entry in view.drift}
        return WorkspaceSharedMaterialsResponse(
            workspace_id=workspace_id,
            map=(
                SharedMaterialsMapView(
                    version=1,
                    materials=[
                        SharedMaterialMapping(
                            source=material.source,
                            skills=[
                                SharedMaterialSkillDrift(skill=d.skill, status=d.status)
                                for d in drift_by_source.get(material.source, ())
                            ],
                        )
                        for material in materials
                    ],
                )
                if view.shared_map is not None
                else None
            ),
            files=[SharedMaterialFileEntry(**vars(entry)) for entry in view.files],
        )

    @router.get(
        "/workspaces/{workspace_id}/skills-shared/file",
        response_model=SharedMaterialFileContent,
    )
    def get_shared_material_file(workspace_id: str, path: str) -> SharedMaterialFileContent:
        _require_workspace(workspace_id)
        try:
            content, size, truncated = read_shared_file_content(workspace_id, path)
        except JobServiceError as exc:
            raise_job_http_error(exc)
        return SharedMaterialFileContent(path=path, size=size, content=content, truncated=truncated)

    return router
