from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from server.app.jobs import JobQueries


def project_current_user_roles(
    workspaces: list[dict[str, Any]],
    current_user: dict[str, Any] | None,
    job_db: JobQueries,
) -> list[dict[str, Any]]:
    if current_user is None:
        return workspaces
    if current_user.get("role") == "admin":
        unrestricted_role = "admin"
    elif current_user.get("actor_scope") == "api":
        unrestricted_role = "editor"
    else:
        unrestricted_role = ""
    role_map = (
        {} if unrestricted_role else job_db.list_user_workspace_roles(str(current_user["id"]))
    )
    return [
        {
            **workspace,
            "current_user_role": unrestricted_role or role_map.get(str(workspace["id"])),
        }
        for workspace in workspaces
    ]
