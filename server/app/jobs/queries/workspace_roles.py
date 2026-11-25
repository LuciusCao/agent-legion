from __future__ import annotations

from server.app.jobs.queries.connection import ConnectionQueriesMixin


class WorkspaceRoleQueriesMixin(ConnectionQueriesMixin):
    def list_user_workspace_roles(self, user_id: str) -> dict[str, str]:
        with self._connect_read() as conn:
            rows = conn.execute(
                "select workspace_id, role from workspace_members where user_id=%s",
                (user_id,),
            )
            return {str(row["workspace_id"]): str(row["role"]) for row in rows}
