from __future__ import annotations

from typing import Any

from server.app.jobs.queries.base import JobQueriesBase


class WorkflowRevisionQueriesMixin(JobQueriesBase):
    def create_workflow_revision(
        self,
        *,
        revision_id: str,
        workspace_id: str,
        workflow_key: str,
        version: int,
        status: str,
        definition_json: str,
        definition_hash: str,
    ) -> dict[str, Any]:
        with self.connect() as conn:
            if status == "active":
                conn.execute(
                    """
                    update workflow_revisions
                    set status='archived'
                    where workspace_id=? and workflow_key=? and status='active'
                    """,
                    (workspace_id, workflow_key),
                )
            conn.execute(
                """
                insert into workflow_revisions(
                  id, workspace_id, workflow_key, version, status, definition_json, definition_hash, published_at
                )
                values (?, ?, ?, ?, ?, ?, ?, case when ?='active' then current_timestamp else null end)
                """,
                (
                    revision_id,
                    workspace_id,
                    workflow_key,
                    version,
                    status,
                    definition_json,
                    definition_hash,
                    status,
                ),
            )
            row = conn.execute(
                "select * from workflow_revisions where id=?", (revision_id,)
            ).fetchone()
        if row is None:
            raise RuntimeError("workflow revision insert did not return a row")
        return dict(row)

    def get_active_workflow_revision(
        self, workspace_id: str, workflow_key: str
    ) -> dict[str, Any] | None:
        with self._connect_read() as conn:
            row = conn.execute(
                """
                select * from workflow_revisions
                where workspace_id=? and workflow_key=? and status='active'
                order by version desc
                limit 1
                """,
                (workspace_id, workflow_key),
            ).fetchone()
        return dict(row) if row else None

    def get_workflow_revision(
        self,
        workspace_id: str,
        workflow_key: str,
        revision_id: str,
    ) -> dict[str, Any] | None:
        with self._connect_read() as conn:
            row = conn.execute(
                """
                select * from workflow_revisions
                where id=? and workspace_id=? and workflow_key=?
                limit 1
                """,
                (revision_id, workspace_id, workflow_key),
            ).fetchone()
        return dict(row) if row else None

    def next_workflow_revision_version(self, workspace_id: str, workflow_key: str) -> int:
        with self._connect_read() as conn:
            row = conn.execute(
                """
                select coalesce(max(version), 0) + 1 as next_version
                from workflow_revisions
                where workspace_id=? and workflow_key=?
                """,
                (workspace_id, workflow_key),
            ).fetchone()
        return int(row["next_version"]) if row is not None else 1

    def list_workflow_revisions(self, workspace_id: str, workflow_key: str) -> list[dict[str, Any]]:
        with self._connect_read() as conn:
            rows = conn.execute(
                """
                select * from workflow_revisions
                where workspace_id=? and workflow_key=?
                order by version desc
                """,
                (workspace_id, workflow_key),
            ).fetchall()
        return [dict(row) for row in rows]
