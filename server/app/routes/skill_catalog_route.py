from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Request

from server.app.auth.skill_access import require_skill_key_in_workspace, resolve_skill_key_owner
from server.app.db.dialect import ConnectSource
from server.app.routes.skill_contracts import SkillDetailResponse
from server.app.services.skill_catalog import SkillCatalogService
from server.app.settings import Settings


def create_skill_catalog_router(
    settings: Settings, connect_source: ConnectSource | None = None
) -> APIRouter:
    """``connect_source``: JobQueries facade (or bare DSN) for the skill
    catalog store — BOUNDARY-DATA-001, #187; falls back to the settings DSN."""
    router = APIRouter()

    def _skills() -> SkillCatalogService:
        # Per-request build: base_dir resolves at call time (HOME may be
        # monkeypatched in tests, same laziness as the skills router).
        return SkillCatalogService(connect_source or settings.database_url)

    @router.get("/agent-catalog/skills/{skill_key:path}", response_model=SkillDetailResponse)
    def get_skill(
        skill_key: str,
        request: Request,
        workspace_id: Annotated[str, Query(min_length=1)],
        ref: str | None = None,
    ) -> SkillDetailResponse:
        # The router-level guard owns query-scope membership and binding.
        job_db = request.app.state.job_db
        require_skill_key_in_workspace(job_db, skill_key, workspace_id)
        # Missing group and foreign workspace-owned keys share one 404 shape.
        if resolve_skill_key_owner(job_db, skill_key) is None and not _skills().has_dir(skill_key):
            raise HTTPException(status_code=404, detail="Skill not found")
        # ref (a git tag of the skill repo) previews that tag's content;
        # an unknown tag is a 404 (see SkillDetailResponse).
        return SkillDetailResponse(**_skills().detail(skill_key, ref=ref))

    return router
