"""Studio-agent skill tool endpoints (issue #217).

Skill read/validate/save-version for the built-in Studio authoring agent.
Skills are workspace-scoped (#710 follow-up, product decision 2026-09-17:
skill 归属是 workspace 级隔离的): the endpoints mirror the job-tools
surface — ``require_studio_agent_scope`` + ``require_studio_agent_workspace``
— so a session-bound run token (schema v45) cannot read, validate, or
version a foreign workspace's skills, exactly like ``create_skill`` (#633)
and the shared-material tools below already were. ``save_skill_version``
is draft-only by design: it commits and tags the skill's LOCAL in-place
repo but never touches the DB skill lock — publishing (re-pin + relock)
stays a human admin action. The module also mounts the workspace-scoped
shared-material tool router (#633), whose endpoints are workspace-bound by
their own guards.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from server.app.auth.dependencies import (
    require_studio_agent_scope,
    require_studio_agent_workspace,
)
from server.app.jobs import JobQueries
from server.app.routes.job_http import raise_job_http_error
from server.app.routes.skill_contracts import SkillDetailResponse
from server.app.routes.studio_agent_skill_contracts import (
    SkillSaveVersionRequest,
    SkillSaveVersionResponse,
    SkillValidateToolResponse,
)
from server.app.services.job_errors import JobServiceError, NotFoundError
from server.app.services.skill_catalog import SkillCatalogService
from server.app.services.skill_editing import SkillEditingService, SkillFileWrite
from server.app.settings import Settings


def create_studio_agent_skill_tools_router(job_db: JobQueries, settings: Settings) -> APIRouter:
    from server.app.routes.studio_agent_shared_tools import (
        create_studio_agent_shared_tools_router,
    )

    router = APIRouter(
        dependencies=[
            Depends(require_studio_agent_scope),
            Depends(require_studio_agent_workspace),
        ]
    )
    catalog = SkillCatalogService(job_db)
    editing = SkillEditingService(runs_dir=settings.skills_runs_dir)

    @router.get(
        "/studio-agent/tools/workspaces/{workspace_id}/skills/{skill_key:path}",
        response_model=SkillDetailResponse,
    )
    def get_skill(workspace_id: str, skill_key: str, ref: str | None = None) -> SkillDetailResponse:
        try:
            _require_skill_in_workspace(job_db, skill_key, workspace_id)
            return SkillDetailResponse(**catalog.detail(skill_key, ref=ref))
        except JobServiceError as exc:
            raise_job_http_error(exc)

    @router.post(
        "/studio-agent/tools/workspaces/{workspace_id}/skills/{skill_key:path}/validate",
        response_model=SkillValidateToolResponse,
    )
    def validate_skill(workspace_id: str, skill_key: str) -> SkillValidateToolResponse:
        try:
            _require_skill_in_workspace(job_db, skill_key, workspace_id)
            return SkillValidateToolResponse(**editing.validate(skill_key))
        except JobServiceError as exc:
            raise_job_http_error(exc)

    @router.post(
        "/studio-agent/tools/workspaces/{workspace_id}/skills/{skill_key:path}/versions",
        response_model=SkillSaveVersionResponse,
        status_code=201,
    )
    def save_skill_version(
        workspace_id: str, skill_key: str, payload: SkillSaveVersionRequest
    ) -> SkillSaveVersionResponse:
        files = [SkillFileWrite(path=item.path, content=item.content) for item in payload.files]
        try:
            _require_skill_in_workspace(job_db, skill_key, workspace_id)
            result = editing.save_version(skill_key, files, payload.new_tag, payload.message)
        except JobServiceError as exc:
            raise_job_http_error(exc)
        assert result is not None  # None only when prepare skips (not used here)
        return SkillSaveVersionResponse(**result)

    # Workspace-scoped shared-material tools (#633): workspace-bound (the
    # router carries its own scope/binding guards), mounted here — the
    # skill-authoring tool surface's natural home — because the assembly
    # modules sit at frozen budget ceilings.
    router.include_router(create_studio_agent_shared_tools_router(job_db, settings))
    return router


def _require_skill_in_workspace(job_db: JobQueries, skill_key: str, workspace_id: str) -> None:
    """Key/path agreement for the skill tool surface (#710, codex P2 on
    #745): a skill key's first segment is a GROUP name, not necessarily a
    workspace id (the demo ships group ``education-video-problems-generation``
    under workspace ``education_video_problems_generation``). The router-level
    guards already pin the caller to one workspace; this check adds the
    workspace-directory strictness — when the key's first segment IS an
    existing workspace's id (the create_skill layout), the key must belong
    to the caller's workspace, so a bound token cannot reach into a foreign
    workspace's skill repo through a mismatched key. Group directories stay
    reachable from any workspace the caller is bound to (shared authoring
    surface, demo layout)."""
    key_workspace = skill_key.partition("/")[0]
    if key_workspace == workspace_id:
        return
    if job_db.get_workspace(key_workspace) is not None:
        raise NotFoundError(f"Skill not found in workspace {workspace_id}")
