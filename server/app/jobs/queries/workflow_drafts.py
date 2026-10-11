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
is the single post-publish authority — conditional on the locked row still
matching the published content's identity hash (#1226 P1: a concurrently
saved newer draft survives as the unpublished draft). The delete also
records a publish-generation tombstone (#1226 五轮 P1, see
``workflow_draft_tombstones.py``): a stale in-flight save (base ≤ the
deleted row's ``updated_at``) can no longer re-insert the pre-publish
content after the commit — it conflicts; ``never-saved`` bases insert
freely; the tombstone is cleared by the first successful write.
"""

from __future__ import annotations

from typing import Any

from server.app.db.connection import DatabaseConnection
from server.app.jobs.queries.connection import ConnectionQueriesMixin
from server.app.jobs.queries.workflow_draft_tombstones import (
    clear_workflow_draft_tombstone,
    record_workflow_draft_tombstone,
)


class DraftConflictError(Exception):
    """The stored draft's updated_at no longer matches the expected value (#633).

    Raised only by the CAS upsert — a mismatched timestamp means another
    session (human editor, another agent turn) saved a newer draft; the
    caller must re-read and rebase, never silently overwrite.
    """


# Marker for "no draft was persisted yet" on the CAS path: a caller holding
# the marker may insert only while the row is still absent (an existing row
# turns the conflict-update branch into a no-op → DraftConflictError). A
# caller holding a real TIMESTAMP whose row was deleted by a publish is
# refused by the tombstone probe (#1226 五轮 P1: base ≤ the deleted row's
# updated_at is a pre-publish generation write, 409 instead of silent
# resurrection). Re-exported by the service layer (workflow_draft_store) so
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
            # #1226 五轮 P1 lifecycle: a successful write ends the publish
            # tombstone window — the row exists again, the tombstone fired
            # (a no-op when the workspace has none).
            clear_workflow_draft_tombstone(conn, workspace_id)
        if row is None:
            raise RuntimeError("workflow draft upsert did not return a row")
        return dict(row)

    def read_workspace_workflow_draft_for_update(
        self, conn: DatabaseConnection, workspace_id: str
    ) -> dict[str, Any] | None:
        """The draft row under a row lock, inside the caller's transaction.

        The publish delete hook reads here before deciding: ``for update``
        serializes against a concurrent draft upsert (READ COMMITTED
        re-evaluates after the lock wait), so the comparison the caller makes
        on the returned content is race-safe. Never commits on its own.
        """
        row = conn.execute(
            f"select {self._DRAFT_COLUMNS}"
            " from workspace_workflow_drafts where workspace_id=%s for update",
            (workspace_id,),
        ).fetchone()
        return dict(row) if row is not None else None

    def delete_workspace_workflow_draft(self, conn: DatabaseConnection, workspace_id: str) -> None:
        """Delete the draft row + record the publish tombstone, caller's transaction.

        Callers decide whether the delete is warranted (publish compares the
        locked row's identity hash against the published content first —
        #1226 P1); this is the plain removal primitive. The tombstone
        (#1226 五轮 P1) records the deleted row's ``updated_at`` so a stale
        in-flight save can no longer re-INSERT the pre-publish content after
        the commit. Never commits on its own; deleting an absent row is a
        no-op (no row, no tombstone — nothing was there to guard).
        """
        row = conn.execute(
            "delete from workspace_workflow_drafts where workspace_id=%s returning updated_at",
            (workspace_id,),
        ).fetchone()
        if row is not None:
            record_workflow_draft_tombstone(conn, workspace_id, row["updated_at"])
