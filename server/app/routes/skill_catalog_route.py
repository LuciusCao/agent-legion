from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from server.app.auth.dependencies import get_current_user
from server.app.db.dialect import ConnectSource
from server.app.jobs import JobQueries
from server.app.routes.job_http import raise_job_http_error
from server.app.routes.skill_contracts import SkillDetailResponse
from server.app.services.job_errors import JobServiceError
from server.app.services.skill_catalog import SkillCatalogService
from server.app.settings import Settings


def require_skill_scope_binding(
    workspace_id: str,
    user: Annotated[dict[str, Any], Depends(get_current_user)],
) -> None:
    """Scoped-token binding for the query-scoped skill surfaces (#710,
    codex P1 on #745).

    The router-level ``require_workspace_access`` already membership-checks
    a non-empty ``workspace_id`` query parameter — what it does not do is
    honor a scoped token's workspace binding, so a run token minted for
    workspace A (even by an admin, who is a member everywhere) could read
    workspace B's skills through these routes. The binding runs regardless
    of role: scoped tokens inherit the minter's role, so admin-minted
    bindings stay bound too (same shape as require_job_workspace_access)."""
    bound = user.get("scoped_workspace_id")
    if bound and str(bound) != str(workspace_id):
        # Same detail as the router guard's refusal: a differing string
        # would let callers distinguish refusal reasons.
        raise HTTPException(status_code=404, detail="Workspace not found")


def resolve_skill_key_owner(job_db: JobQueries, skill_key: str) -> str | None:
    """The workspace a skill key's first segment belongs to, or None for a
    group directory (#710, red-team R8 on #745).

    The first segment is matched against existing workspace ids TWICE: an
    exact match (the create_skill layout ``<workspace_id>/<name>``), then a
    case-insensitive sweep. The second pass closes the case-variant bypass
    red-team R8 reproduced on case-insensitive filesystems (macOS APFS):
    ``WS_Victim/...`` misses the exact Postgres lookup, was treated as a
    group directory, and the FS then resolved it to the real
    ``ws_victim/...`` repo. A case-insensitive hit is refused outright — it
    is never legitimate (workspace ids are lowercase by schema v62), and
    returning the matched owner would silently redirect the read."""
    key_workspace = skill_key.partition("/")[0]
    if job_db.get_workspace(key_workspace) is not None:
        return key_workspace
    if key_workspace.lower() != key_workspace:
        # Uppercase/mixed-case variant of some workspace id? Any workspace
        # id whose lowercase form equals this segment's makes it a variant.
        for candidate in _lowercase_workspace_ids(job_db):
            if candidate == key_workspace.lower():
                raise _SKILL_NOT_FOUND
    return None


_SKILL_NOT_FOUND = HTTPException(status_code=404, detail="Skill not found")


def _lowercase_workspace_ids(job_db: JobQueries) -> list[str]:
    return [str(row["id"]).lower() for row in job_db.list_workspaces()]


def require_skill_key_in_workspace(job_db: JobQueries, skill_key: str, workspace_id: str) -> None:
    """Read-side ownership for skill keys (#710, codex P2 + red-team R8).

    The authorization scope is the requested workspace (membership-checked
    by the router-level guard; ``require_skill_scope_binding`` pins scoped
    tokens to their binding). On top of that: when the key's first segment
    belongs to an existing workspace (create_skill layout), the key is that
    workspace's private asset — members of other workspaces get 404 even
    through their own scope. Group directories (first segment not owned by
    any workspace, e.g. the demo's hyphenated group) are shared READ
    surfaces; their WRITE surface is admin-only (see
    studio_agent_skill_tools)."""
    owner = resolve_skill_key_owner(job_db, skill_key)
    if owner is not None and owner != str(workspace_id):
        raise _SKILL_NOT_FOUND


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
        user: Annotated[dict[str, Any], Depends(get_current_user)],
        ref: str | None = None,
    ) -> SkillDetailResponse:
        # The query parameter doubles as the membership scope for the
        # router-level guard (same pattern as the /agent-catalog list
        # endpoint); min_length=1 keeps an empty value from skipping that
        # check (red-team R8: empty workspace_id fail-opened the guard).
        require_skill_scope_binding(workspace_id, user)
        require_skill_key_in_workspace(request.app.state.job_db, skill_key, workspace_id)
        try:
            # ref (a git tag of the skill repo) previews that tag's content;
            # an unknown tag is a 404 (see SkillDetailResponse).
            return SkillDetailResponse(**_skills().detail(skill_key, ref=ref))
        except JobServiceError as exc:
            raise_job_http_error(exc)

    return router
