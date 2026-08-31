"""Workflow revision snapshot reads on the JobQueries facade (#287).

Why a separate module: the revision *read* surface (active lookup, detail by
id, version history, next-version counter) serves dispatch, intake, and the
Studio API, while ``workflow_revisions.py`` keeps the transactional write
path (publish) whose body must stay coupled to the projection writes in
``workflow_revision_projection.py``. ``WorkflowRevisionQueriesMixin``
inherits this mixin so the composed JobQueries surface is unchanged.

#211 Phase 3 (read-layer binding): every predicate here binds workspace_id
instead of the workflow_key column — the publish guard
(require_draft_workflow_key_match, DB-WORKSPACE-KEY-BINDING-001) makes the
two equal on every row, so the column is redundant as a filter. Signature
parameters keep the historical workflow_key name for callers; the column
itself drops in Phase 4.
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
                where workspace_id=%s and status='active'
                order by version desc
                limit 1
                """,
                (workspace_id,),
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
                where id=%s and workspace_id=%s
                limit 1
                """,
                (revision_id, workspace_id),
            ).fetchone()
        return dict(row) if row else None

    def next_workflow_revision_version(self, workspace_id: str, workflow_key: str) -> int:
        with self._connect_read() as conn:
            row = conn.execute(
                """
                select coalesce(max(version), 0) + 1 as next_version
                from workflow_revisions
                where workspace_id=%s
                """,
                (workspace_id,),
            ).fetchone()
        return int(row["next_version"]) if row is not None else 1

    def list_workflow_revisions(self, workspace_id: str, workflow_key: str) -> list[dict[str, Any]]:
        with self._connect_read() as conn:
            rows = conn.execute(
                """
                select * from workflow_revisions
                where workspace_id=%s
                order by version desc
                """,
                (workspace_id,),
            ).fetchall()
        return [dict(row) for row in rows]
