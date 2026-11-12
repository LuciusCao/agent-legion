"""Skill ownership guards shared by catalog, editor and Studio tool routes.

The router-level workspace guards decide authentication, token binding and
membership first. This module only agrees the addressed skill with that
already-authorized workspace:

- an exact existing workspace first segment makes the key private to it;
- a case variant of a workspace id is refused, never treated as a group;
- any other first segment is a shared group, readable but not Studio-writable;
- absolute paths contribute a key only when they resolve inside the skills
  base and contain both group/workspace and skill segments.

Workspace/key mismatches use the route family's existing 404 shape. No helper
here is an authorization shortcut: callers must retain their router guards.
"""

from __future__ import annotations

from pathlib import Path

from fastapi.exceptions import HTTPException

from server.app.jobs import JobQueries
from server.app.services.job_errors import NotFoundError


def _skill_not_found() -> HTTPException:
    # A fresh exception avoids retaining traceback chains across requests.
    return HTTPException(status_code=404, detail="Skill not found")


def resolve_skill_key_owner(job_db: JobQueries, skill_key: str) -> str | None:
    """Return an exact workspace owner; refuse case variants and pass groups through."""
    key_workspace = skill_key.partition("/")[0]
    if job_db.get_workspace(key_workspace) is not None:
        return key_workspace
    if key_workspace.lower() != key_workspace:
        workspace_ids = {str(row["id"]).lower() for row in job_db.list_workspaces()}
        if key_workspace.lower() in workspace_ids:
            raise _skill_not_found()
    return None


def require_skill_key_in_workspace(job_db: JobQueries, skill_key: str, workspace_id: str) -> None:
    """Workspace-owned keys are private; non-workspace group keys are shared reads."""
    owner = resolve_skill_key_owner(job_db, skill_key)
    if owner is not None and owner != str(workspace_id):
        raise _skill_not_found()


def require_skill_path_in_workspace(
    job_db: JobQueries, raw_path: str, base_dir: Path, workspace_id: str
) -> None:
    """Extract a two-segment key from an absolute path, then apply key ownership."""
    try:
        resolved_base = base_dir.expanduser().resolve()
        relative = Path(raw_path.strip()).expanduser().resolve().relative_to(resolved_base)
    except ValueError:
        return
    if len(relative.parts) >= 2:
        require_skill_key_in_workspace(job_db, "/".join(relative.parts[:2]), workspace_id)


def require_skill_writable_in_workspace(
    job_db: JobQueries, skill_key: str, workspace_id: str
) -> None:
    """Studio agents may write workspace-owned keys, never shared group keys."""
    owner = resolve_skill_key_owner(job_db, skill_key)
    if owner is None:
        raise NotFoundError("Group skills are read-only for studio agents")
    if owner != workspace_id:
        raise NotFoundError(f"Skill not found in workspace {workspace_id}")
