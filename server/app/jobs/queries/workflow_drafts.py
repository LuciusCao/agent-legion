"""Persistence for the Studio workflow YAML draft (schema v61).

One row per workspace (``workspace_workflow_drafts``): the Studio editor's
single-draft model. The write path is a single-statement upsert — no
read-then-write — so concurrent autosaves from two tabs degenerate to plain
last-write-wins instead of losing an update.

#633 adds the opt-in compare-and-set variant (``workflow_draft_cas.py``,
split for the budget): an ``expected_updated_at`` that no longer matches
the stored row raises ``DraftConflictError`` instead of overwriting a
newer draft. #1221 adds the delete: a successful draft publish removes the
row inside the revision write's own transaction, so the published revision
is the single post-publish authority.
"""

from __future__ import annotations

from typing import Any

from server.app.db.connection import DatabaseConnection
from server.app.jobs.queries.connection import ConnectionQueriesMixin


class DraftConflictError(Exception):
    """The stored draft's updated_at no longer matches the expected value (#633).

    Raised only by the CAS upsert — a mismatched timestamp means another
    session (human editor, another agent turn) saved a newer draft; the
    caller must re-read and rebase, never silently overwrite.
    """


# Marker for "no draft was persisted yet" on the CAS path: a caller holding
# the marker may insert only while the row is still absent (an existing row
# turns the conflict-update branch into a no-op → DraftConflictError). A
# caller holding a real timestamp whose row was DELETED underneath (publish
# deletes the draft row, #1221) silently recreates the draft on insert —
# allowed on purpose (#1221 §4 方案 B): the recreation reads as a fresh
# draft. Re-exported by the service layer (workflow_draft_store) so
# routes/clients depend on services only.
DRAFT_NEVER_SAVED = "never-saved"


class WorkflowDraftQueriesMixin(ConnectionQueriesMixin):
    """CRUD for workspace_workflow_drafts (get / upsert / delete)."""

    _DRAFT_COLUMNS = "workspace_id, definition_yaml, created_at, updated_at"

    def get_workspace_workflow_draft(self, workspace_id: str) -> dict[str, Any] | None:
        with self._connect_read() as conn:
            row = conn.execute(
                f"select {self._DRAFT_COLUMNS}"
                " from workspace_workflow_drafts where workspace_id=%s",
                (workspace_id,),
            ).fetchone()
        return dict(row) if row is not None else None

    def upsert_workspace_workflow_draft(
        self, workspace_id: str, definition_yaml: str
    ) -> dict[str, Any]:
        with self.connect() as conn:
            row = conn.execute(
                """
                insert into workspace_workflow_drafts(workspace_id, definition_yaml)
                values (%s, %s)
                on conflict(workspace_id) do update set
                  definition_yaml=excluded.definition_yaml,
                  updated_at=current_timestamp
                returning workspace_id, definition_yaml, created_at, updated_at
                """,
                (workspace_id, definition_yaml),
            ).fetchone()
        if row is None:
            raise RuntimeError("workflow draft upsert did not return a row")
        return dict(row)

    def delete_workspace_workflow_draft(self, conn: DatabaseConnection, workspace_id: str) -> None:
        """Delete the workspace's draft row inside the caller's transaction (#1221).

        Publish rides this on the revision write's commit hook: the draft row
        dies atomically with the revision that replaces it as the single
        authority (a delete failure rolls the whole publish back). Never
        commits on its own; deleting an absent row is a no-op.
        """
        conn.execute(
            "delete from workspace_workflow_drafts where workspace_id=%s",
            (workspace_id,),
        )
