"""Workflow revision snapshot reads on the JobQueries facade (#287).

Why a separate module: the revision *read* surface (active lookup, detail by
id, version history, next-version counter) serves dispatch, intake, and the
Studio API, while ``workflow_revisions.py`` keeps the transactional write
path (publish) whose body must stay coupled to the projection writes in
``workflow_revision_projection.py``. ``WorkflowRevisionQueriesMixin``
inherits this mixin so the composed JobQueries surface is unchanged.
"""

from __future__ import annotations

from typing import Any

from server.app.jobs.queries.connection import ConnectionQueriesMixin


class WorkflowRevisionReadQueriesMixin(ConnectionQueriesMixin):
    """Read-only queries for ``workflow_revisions`` rows."""

    def get_active_workflow_revision(
        self, workspace_id: str, workflow_key: str
    ) -> dict[str, Any] | None:
        with self._connect_read() as conn:
            row = conn.execute(
                """
                select * from workflow_revisions
                where workspace_id=%s and workflow_key=%s and status='active'
                order by version desc
                limit 1
                """,
                (workspace_id, workflow_key),
            ).fetchone()
        return dict(row) if row else None

    def list_active_workflow_revisions(self) -> list[dict[str, Any]]:
        """All active revisions across workspaces (at most one per workspace/workflow)."""
        with self._connect_read() as conn:
            rows = conn.execute(
                """
                select * from workflow_revisions
                where status='active'
                order by workspace_id, workflow_key
                """,
            ).fetchall()
        return [dict(row) for row in rows]

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
                where id=%s and workspace_id=%s and workflow_key=%s
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
                where workspace_id=%s and workflow_key=%s
                """,
                (workspace_id, workflow_key),
            ).fetchone()
        return int(row["next_version"]) if row is not None else 1

    def list_workflow_revisions(self, workspace_id: str, workflow_key: str) -> list[dict[str, Any]]:
        with self._connect_read() as conn:
            rows = conn.execute(
                """
                select * from workflow_revisions
                where workspace_id=%s and workflow_key=%s
                order by version desc
                """,
                (workspace_id, workflow_key),
            ).fetchall()
        return [dict(row) for row in rows]
